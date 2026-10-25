# dotnet-ios-linux project specification

Status: experimental; private/local M2–M9 device-path evidence is recorded,
sanitized receipt publication is pending, and the root bootstrap remains a
candidate under validation.
Date: 2026-10-17; last updated 2026-10-25.
Target host: glibc Linux x86_64; current evidence host is EndeavourOS/Arch.
Decision basis: accepted [architecture decision records](decisions/README.md).
The “Linux Mac Development Setup” conversation
`6a68da62-2fbc-83eb-ad21-0f2dd79c7ce6` is historical input, not the current
decision record.

## How to read this document

This specification is the normative architecture, scope, and acceptance-policy
document. It does not itself prove an experimental result.

- The [compatibility matrix](status/compatibility-matrix.md) is the concise
  current-support source and labels unpublished evidence explicitly.
- [Decision records](decisions/README.md) preserve why accepted architectural
  choices were made.
- The [evidence area](evidence/README.md) defines receipt publication and keeps
  historical observations distinct from auditable records.
- The root [README](../README.md) and
  [operator runbook](runbooks/installation-and-device-troubleshooting.md) are
  the operational entry points.

## 1. Outcome and architectural rule

Build an ordinary .NET iOS application on Linux, produce a development-signed
application bundle, install it over USB, and launch it on a physical iPhone. The
intended post-setup workflow uses ordinary `dotnet` and MSBuild; Rider and stock
`uno-check` are separate consumer goals and are not yet demonstrated.

**Compatibility before customization:** when upstream expects a workload, SDK pack, file, executable, environment variable, or build target, reproduce that interface before changing upstream code. Fork only where a host dependency cannot be satisfied by configuration, a compatible implementation, or a narrowly scoped shim.

This is an experimental Linux host implementation for the macios build pipeline. A synthetic Xcode tree does not make Linux macOS, and a successfully resolved workload does not prove that its tasks or native tools execute on Linux. Each boundary below requires evidence.

The first technical success is an unsigned minimal UIKit application. The first device MVP is that application signed, installed, and visibly running on an iPhone without a Mac participating in the build, signing, or deployment. A Mac may supply the initial SDK extraction and reference-build evidence.

## 2. Fixed decisions and open hypotheses

| Area | Decision | Current evidence / remaining gate |
| --- | --- | --- |
| SDK root | Root containing the real active host `dotnet` executable | Writable-root merge contracts pass; constrained system-root contracts pass; live package-manager-root qualification remains |
| Workload identity | Direct registrations are `ios`, `macos`, `maccatalyst`, and `tvos`; only `ios` is Linux runtime/device-qualified | One aggregate CLI transaction installs the selected registrations in a writable root; `--keep-existing` preserves an exact lock-compatible direct external registration |
| Apple companion workloads | Exact locked registration-only derivatives for macOS, Mac Catalyst, and tvOS | No Linux companion native-bundle, signing, deployment, or runtime claim; see [ADR-0008](decisions/0008-register-locked-apple-companions.md) |
| Manifest | Deterministic unsigned derivatives of the four direct Apple manifest packages at `27.0.10722` | Disposable-root all-four install/list/restore/uninstall evidence; iOS alone has a Linux execution graph |
| Microsoft packs | Reuse exact official SDK, reference, runtime, task, and template packs | The selected direct Apple package closures are hash-locked; broader app coverage remains |
| AOT | Package the exact LLVM-enabled Linux-hosted Mono cross compiler for iOS arm64 | Full app link and repeated physical-device probes passed |
| Xcode | Synthetic layout selected by `DEVELOPER_DIR` | Pinned macios discovery/native link passed; broader Xcode tool coverage remains |
| Darwin backend | Project-owned Linux adapters and independently pinned host tools | Minimal Xcode 27 tool, signing, and deployment slice passed |
| Uno environment check | Target: stock binary with a custom manifest | Pending qualification: Linux iOS check must run rather than be silently filtered out |
| IDE | Stock Rider with receipt-bound host CLI | Target: evaluation/build; Run/debug remain separate gates |

Unless a row links to a published receipt, its completed-evidence wording refers
to private/local observations summarized in the status matrix, not independently
auditable public evidence.

Earlier proposals for workload ID `ios-linux` and a separate `~/.dotnet-ios-linux` SDK installation are superseded. `dotnet-ios-linux` remains the project/bootstrap name. No global operating-system spoofing is permitted.

## 3. Scope and version policy

### MVP matrix

| Dimension | Initial scope |
| --- | --- |
| Host | Linux x86_64 with glibc; current validation host is EndeavourOS/Arch |
| Framework and RID | `net10.0-ios`, `ios-arm64` |
| Runtime | Mono with device AOT; no JIT dependency |
| Configuration | Release first; Debug compatibility investigated separately |
| Application | Minimal UIKit app before Uno |
| Apple toolchain | Xcode 27.0 / iPhoneOS 27.0 SDK family |
| Device | One recorded physical iPhone model and OS build, connected by USB |
| Outputs | Unsigned `.app`, then development-signed `.app`; `.ipa` when needed by deployment |
| Build host independence | No remote Mac build service after SDK acquisition |

The active candidate baseline was rebaselined on 2026-10-17 to the supplied Mac
reference: .NET SDK `10.0.402`, workload set `10.0.401.1`, macios package
version `27.0.10722`, Xcode `27.0 (27A266a)`, and iPhoneOS SDK `27.0 (24A430)`.
Private/local evidence records SDK discovery, the complete direct workload graph,
workload lifecycle, matched LLVM AOT execution, native compilation/linking,
unsigned bundling, direct development signing, USB installation, clean and
incremental manual launches, cold relaunch, visible UI, and deterministic runtime
probes for the minimal UIKit application. Real Android, MAUI Android, and Wasm
workloads also survived an iOS install/uninstall cycle in a disposable canonical
SDK copy. Those observations are summarized in the status matrix but remain
unauditable until sanitized receipts are published. Automated launch, negative
deployment paths, Rider, uno-check, and Uno remain unverified. Preserve the
superseded `10.0.302` / `26.5.10301` material only as historical summary; do not
mix the two graphs.

`toolchain.lock.json` records the SDK version and hash, SDK feature band, workload installation mode/set, manifest IDs and versions, every direct package ID/version/hash, runtime source commit, compiler and LLVM outputs, Apple SDK version/build/hash, Xcode metadata, independently selected host-tool revisions, portable Swift/Python dependencies, signing/deployment tools, and remaining evidence gaps. The runtime commit is traced to the selected device runtime rather than inferred solely from the SDK version.

Keep SDK version, SDK feature band, manifest package suffix, manifest version, workload-set version, iOS SDK version, and deployment target distinct. For example, SDK `10.0.402` belongs to feature band `10.0.400`; this alone does not establish which manifest package suffix or fallback directory the distribution consumes. Inspect the selected SDK's behavior rather than copying a path blindly.

### Non-goals for the first MVP

- iOS simulator, Linux arm64 or musl hosts, and macOS/Catalyst/tvOS/watchOS
  runtime targets. macOS, Mac Catalyst, and tvOS may be registered from their
  locked derivatives, but registration does not establish target support.
- NativeAOT, CoreCLR, managed breakpoints, hot reload, or a full Xcode replacement.
- Wireless deployment, automatic Apple-account enrollment, or App Store submission.
- General compatibility with all asset catalogs, storyboards, native plugins, or Swift packages.
- A new application SDK, replacement .NET CLI, custom Uno project format, or modified Rider.
- Broad MAUI support. Minimal Uno validation follows the UIKit device proof; the production ChambaXtra application is a later integration target.

## 4. Architecture and responsibilities

Target architecture; the terminal path is the current consumer baseline, while
Rider and stock uno-check remain separate unverified consumer gates:

```text
Rider / terminal / stock uno-check + project manifest
                         |
           receipt-bound host .NET root
                         |
          standard workload ios + MSBuild
                         |
          official Microsoft.iOS.* packs
                         |
       managed compilation / trim / registrar
                         |
      Linux Mono AOT cross compiler -> arm64 objects
                         |
 synthetic Xcode + project-owned adapters + Apple SDK
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

### 4.1 Receipt-bound host .NET environment

The participating SDK/workload root is the directory containing
`realpath "$(command -v dotnet)"`. A writable user root uses the unprivileged
merge lifecycle. A non-writable package-manager root requires a compatible
pre-existing SDK and uses the constrained workload helper defined by
[ADR-0006](decisions/0006-use-the-active-host-dotnet-root.md); the project does
not merge its fallback SDK into that root. Existing SDKs, Android/WebAssembly
workloads, packs, metadata, and history must be inventoried and preserved.

`./install.sh preflight` performs that inventory without invoking `dotnet` or
creating project data/cache roots. It derives Apple-family classifications from
the active workload set, its manifests, and `extends` metadata. Installed
markers remain the sole source of presence. A workload whose closure reaches
only non-iOS Apple roots is reference-only for execution on Linux. Preflight
preserves and reports it without mutation; it does not decide the install plan.
Ambiguous or malformed metadata fails closed. This is an inventory boundary, not
a workload-set upgrade mechanism.

An iOS-derived workload remains unqualified unless it is direct `ios`.
`mixed-unqualified` means a closure with a non-Apple leaf or with `ios` plus
another Apple root. Inventory classification does not create Linux execution
support or modify observed state.

The generated activation file sets the receipt-recorded `DOTNET_ROOT`, puts its
`dotnet` and tool directory on `PATH`, exports `DEVELOPER_DIR`, and adds the
project shim directory for that development environment. Rider must explicitly
select the same executable and receive the same environment when started through
the desktop. Detect conflicting architecture-specific root variables and stale
MSBuild SDK overrides.

Doctor must compare resolved executable paths, `dotnet --info`, selected SDK,
workload graph, privileged ownership state when applicable, and IDE
configuration. A mismatch is an error with a concrete correction, not a reason
to continue against another installation. Temporary roots isolate destructive
lifecycle tests; live package-manager-root mutation requires a separate
disposable-host qualification.

### 4.2 Workload resolution and installation

For a writable root, the installer invokes one standard CLI command for the direct selected workload
set: `dotnet workload install ios macos maccatalyst tvos`. SDK 10.0.402 selects
the exact official manifest packages at `27.0.10722`; distinct project manifest
IDs cannot safely replace those effective graphs. The installer therefore
derives unsigned local packages with those exact IDs/versions from the
hash-verified official packages. The iOS derivative removes unsupported Mono
iOS `extends` edges, declares `linux-x64`, and supplies a direct net10
physical-device graph. Companion derivatives remove unsupported `extends`,
declare `linux-x64`, and preserve an explicit `installed-unqualified` boundary.
Original packages are never modified or redistributed as project source.

`macos`, `maccatalyst`, and `tvos` are **registration-only** on Linux. Their
locked closures are acquired and verified so the CLI can register them, but no
Linux companion compiler, native bundle, signing, deployment, launch, runtime,
Rider, or Uno support is claimed. A workload that reaches `ios` indirectly, or
reaches `ios` plus another Apple root, or reaches both Apple and non-Apple
targets, is unqualified; it is not broad MAUI
support and requires independent consumer evidence.

The iOS graph contains the iOS 27 SDK/reference/generic runtime/device
runtime/template packs, the iOS 26 net10 SDK fallback required by libraries
without an explicit `TargetPlatformVersion`, runtime `10.0.12`
Mono/device/task/targets packs, and the two distinctly named project packs.
Companion graphs contain their exact official package closures and their one
local derivative each. `toolchain.lock.json` records SHA-256 and SHA-512 for
each selected archive. `verify-workload-packages.py` rehashes local derivatives,
project packages, and installed official archives after installation and during
doctor. It requires official NuGet.org archives to pass author/repository
signature verification in offline revocation mode and verifies local deterministic
packages remain unsigned.

The bootstrap stages and validates the complete graph before activation. Project package names never impersonate official Microsoft packs. The automatically imported compatibility SDK selects `DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64`; integration evidence confirms that macios consumes this physical compiler path.

Manifest-generated imports must load the Linux compatibility pack before the relevant upstream targets consume its values. Preserve official reference and device-runtime contents byte-for-byte. An official SDK pack may contain unusable Mach-O host executables; inventory those separately from its reusable managed assemblies.

The standard CLI owns workload installation records; no records are fabricated by
hand. Isolated test gates pass update, install, list, restore, repair, CLI
uninstall, clean, and reinstall. Production setup uses receipt-backed aggregate
restoration for teardown. It snapshots only selected Apple predecessor closures,
restores prior bytes/metadata, journals unavoidable global `InstalledPacks`
reconciliation without replaying foreign records, and refuses changed protected
payloads. A disposable copy with
real Android, MAUI Android, and Wasm workloads retained those workloads and
shared packs after all-four registration and restoration.

Normal application `global.json` stays standard. Bootstrap workload operations
run beneath a private control `global.json` that pins the resolved installed SDK
patch and compatible installed workload set for the transaction. SDK selection
stays within the lock's supported feature band; workload-set selection requires
an exact match for all four locked direct Apple manifest mappings. The lock's SDK and workload
set remain hash-verified fallbacks when compatible installations are absent in
a writable root. A non-writable root must already contain a compatible SDK, and
the selected workload-set version must equal the lock. No SDK fallback is merged
there; an absent exact workload set is supplied in the verified local-only
bundle and installed by the constrained helper.
Official IDs map to NuGet.org while exact project IDs map to the local feed.
Offline mode blocks downloads and succeeds only with a complete prepopulated
cache.

For a non-writable root, all official and project packages plus the workload-set
package are assembled into a deterministic local tar bundle after lock-hash and
NuGet-signature verification. The root-owned helper validates that inventory
again, rejects pre-existing unowned `ios` state, uses a root-scoped lock and
root-owned journal, and runs privileged `dotnet` commands with an allowlisted
environment in a new network namespace. Verify, recovery, and uninstall require
the random ownership token recorded in both the user receipt and root state.
The allowlist contains only private helper home/cache/temp paths, the selected
`DOTNET_ROOT`, a fixed system `PATH` and locale, .NET first-run/telemetry controls,
and fail-closed loopback proxy values. Workload children inherit the lifecycle
lock descriptor, so helper termination cannot permit recovery while an orphaned
child still mutates the root.

Install and teardown use durable per-operation cursors, same-filesystem removal
tombstones, atomic file replacement, data-before-journal fsync ordering, and an
atomic finalization tombstone. Writable-root production installation invokes the
pinned all-four CLI command. A system-root request is explicitly transformed to
the helper's established iOS-only plan, with companion registration deferred
and `--keep-existing` rejected. Teardown restores the exact
receipt-owned paths and JSON key instead of invoking broad workload update or
uninstall commands.
While checking same-set consumers and restoring paths, the transaction holds the
Linux `flock` honored by SDK `FileShare.None` on
`globaljsonworkloadsets.json`. The owned JSON mutation is published last. Any
other recorded key selecting the same workload set causes exact packs,
manifests, workload-set data, and installation records to be retained.
When a writable-root installation also added the selected SDK fallback, that
intact SDK is retained and its project ownership is relinquished rather than
stranding the recorded same-set consumer.

### 4.3 Stock uno-check

The intended custom manifest must be derived from the pinned upstream schema,
retain relevant existing checks, and add Linux iOS coverage. Its workload entry
must retain `workloadId: ios`, reference the selected effective manifest ID and
package, and use the exact Linux platform token recognized by that uno-check
release. Do not assume .NET RIDs and uno-check platform strings are
interchangeable. This consumer path remains unqualified until its positive and
negative checks pass.

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

Build the Linux tool path only from independently pinned inputs in
`toolchain.lock.json` and project-owned setup scripts. Third-party host-setup
projects are not installer inputs, compatibility authorities, audit baselines,
or reference implementations. Extract the SDK from the user's Xcode
installation, optionally through `mac-build.asfiroth.home` when access is
configured. Do not bundle Apple SDK files in project releases. Retain dependency
licenses and audit redistribution eligibility before publishing binaries.

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

First use project-owned signing and device utilities directly against the
produced bundle. Stage the independently pinned `rcodesign` signing
implementation and `pymobiledevice3` deployment dependency through this
repository. Pin all versions, including Python dependencies.

Require a development identity with its private key, matching provisioning profile, bundle ID, permitted device, team, capabilities, and unexpired validity. Start with user-provided credentials/profiles; account automation is optional later. Sign embedded frameworks/libraries in the required order and verify the final bundle. Do not edit resources after signing. Keep private keys, passwords, tokens, profiles, and pairing records out of source control and public receipts.

USB tests must record pairing/trust, Developer Mode where required, developer-image/tunnel requirements for that OS, installation result, launch result, and application logs. Never infer successful launch from successful installation. Expose actionable diagnostics for a locked/untrusted device, unavailable service, rejected profile, or expired signature.

After M9, integrate proven operations behind ordinary MSBuild run targets where feasible. Building from Rider is a separate test from device discovery, Run, native debugging, and managed debugging. An IDE may enforce host checks before invoking MSBuild; if stock Rider blocks the workflow, document the exact limit rather than claiming automatic compatibility.

Validate a minimal Uno application after the UIKit proof using the same TFM/RID and receipt-bound host SDK. Exercise the resource/native-library paths it adds. Successful `uno-check` alone is not Uno application support. Port ChambaXtra only after that minimal Uno device test passes.

## 5. Bootstrap, lifecycle, and repository layout

Bootstrap is the one custom entry point. Root `install.sh` implements `install`/`setup`, `doctor`, `uninstall`, `--offline`, `--dry-run`, confirmation control, cache control, and build parallelism. It rejects effective UID 0, is lock-serialized, idempotent, fail-closed, and receipt-backed. It targets the active host .NET root, durable `${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux`, and disposable `${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux`. It never edits shell startup files; after success it prints Bash or Zsh commands that make `run-ios-app.sh` globally discoverable.

Writable roots use the ordinary user lifecycle and a root-specific lock stored in
that root. Non-writable roots elevate only validated helper bootstrap operations
and the installed root-owned helper. The helper and trust lock live below
`/usr/local`, recovery state below `/var/lib/dotnet-ios-linux`, and root locks
below `/run/lock/dotnet-ios-linux`. Package acquisition, local feed generation,
bundle assembly, and signature/hash verification finish before elevation. The
helper uses immutable content-addressed installed paths, isolated Python mode, no
inherited execution environment, exact workload identity checks, and fail-closed
network namespace isolation. Completed rollback and uninstall prune privileged
package/cache payloads while retaining a small ownership/provenance tombstone;
hash-matching packs retained by the workload CLI for another workload remain
explicitly recorded rather than being deleted or forgotten.

Install requires an operator-supplied Xcode archive and streams its Xcode,
platform, and iPhoneOS SDK metadata before import. The archive's Xcode
major/minor must match `RecommendedXcodeVersion` read from the selected installed
iOS SDK pack; this deliberately rejects adjacent releases such as Xcode `27.1`
for a workload declaring `27.0`. The observed archive hash and metadata are
recorded rather than compiled into the installer. Install verifies all downloaded
archives, source revisions, generated packages, staged output hashes, the
effective manifest graph, official NuGet signatures, and the LLVM-enabled
compiler before changing the final marker from `installing` to `complete`.
Direct downloads use randomized reserved files, verify hashes before publication,
and atomically publish without replacing a concurrent path. Staged directories
use the same no-clobber rule except for explicit replacement of a verified
project-owned SDK or synthetic bundle, which uses an atomic directory exchange.
The Mono AOT compiler is built in a temporary sibling cache directory and is
published only after its normalized-prefix output hash, matched LLVM payload,
and complete verified provenance record pass. A compiler executable without that
record is incomplete and is discarded rather than reused. The compiler's
source/build/install/LLVM prefixes and locale/time inputs are normalized; the
locked compiler output is accepted only after cross-prefix reproducibility
validation.
`doctor` verifies any installer-owned SDK receipt, the selected pre-existing SDK
when reused, durable file/symlink/mode inventory, workload transaction, all
direct package hashes and official signatures, tool versions, LLVM support,
workload/Xcode compatibility, and synthetic Xcode metadata. A full top-level run
still requires access to an Xcode archive; component and lifecycle paths have
been exercised independently.

Do not hand-edit workload metadata to fabricate installation success. Workload mutations are coordinated with the CLI and protected by a target-root lifecycle lock. Uninstall preflights all receipts, requires the system ownership token when applicable, removes only verified owned entries, restores only paths changed by this install, and preserves later workloads/history and unknown durable files. It never deletes the selected host root. Cache deletion is optional and cannot break durable toolchain execution. Environment activation is explicit and reversible and does not globally intercept unrelated Apple tooling.

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
| **M0 — Evaluate** | Inventory the reference Mac and Linux host; create baseline lock draft; select conflict-free `ios` manifest strategy; evaluate minimal `net10.0-ios` project | Selected host Linux SDK loads intended iOS targets without missing-SDK failure; binlog shows TFM/RID and effective manifest identities; next blocker classified |
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
| Lifecycle | Fresh install, repeated setup, interruption/rollback, repair, restore, uninstall, ownership-token mismatch, supported upgrade | Disposable user roots and disposable system-root hosts; preserve unrelated workloads |
| Coexistence | Android and WebAssembly builds before/after iOS install; non-iOS imports unaffected; conflicting official iOS manifest rejected | Host-root-equivalent test environment |
| Signing | Matching and mismatched identities/profiles, nested code, tampered resources, expiration | Private signing environment |
| Device | USB install, launch, relaunch, runtime probes, resource rendering, logs | Recorded physical iPhone |
| Consumer | Stock uno-check positive/negative cases; Rider evaluation/build; minimal Uno device run | Pinned consumer versions |

Use a reference Mac build of the same sample and baseline to compare task inputs, command contracts, bundle structure, entitlements, architectures, and runtime behavior. Do not demand byte-identical signed artifacts: timestamps, paths, UUIDs, and signatures may differ. Normalize known differences and investigate semantic ones. Reproducibility means the same locked inputs produce equivalent validated outputs; deterministic host-tool binaries are an additional goal where attainable.

Public CI runs SDK-free tests. Private SDK/device tests are explicit gates with receipts, not silently skipped passes. Keep ordinary untrusted PR jobs away from signing identities and pairing material. Establish duration and disk-use baselines during M4/M6, then track regressions; no unmeasured performance claim belongs in the release matrix.

### Acceptance command contracts

These command contracts pass in the isolated writable-root lifecycle gate; the build command also passes with the private imported SDK and proven AOT pack:

```sh
dotnet --info
dotnet workload list
dotnet workload install ios macos maccatalyst tvos
dotnet workload restore tests/HelloIos/HelloIos.csproj
dotnet restore tests/HelloIos/HelloIos.csproj -p:TargetFramework=net10.0-ios -r ios-arm64
dotnet build tests/HelloIos/HelloIos.csproj -f net10.0-ios -r ios-arm64 -c Release
```

`dotnet workload repair`, uninstall, and reinstallation remain isolated-test operations because they affect the installed workload set. The root bootstrap uses the same pinned contracts and ownership transaction. Record the exact `Run` command and device-selection properties only after the selected macios targets and adapter are proven.

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
| Installer damages a shared host root | Other development workloads fail | Target-root lock, sealed offline bundle, root-owned journal, ownership token, rollback/coexistence tests, disposable live-root gate |
| Privileged workload process reaches the network or inherits caller code | Root compromise or unreviewed package acquisition | Fixed installed helper, isolated Python, allowlisted environment, local-only feed, network namespace; fail closed when isolation is unavailable |
| Redistribution rights differ by component | Unshippable binary bundle | User-supplied Apple SDK; per-component provenance/license review |
| Minimal UIKit success overgeneralized to Uno/MAUI | Misleading support scope | Separate application gates and release matrix entries |

No arbitrary calendar estimates are assigned before the compiler and manifest gates pass. Every unresolved hypothesis must have an owner when implementation is staffed, a minimal experiment, and a recorded result.

## 9. Success criteria and release claims

**Unsigned technical proof (M6):** Linux alone restores and builds the minimal UIKit app through normal `dotnet build`, produces the expected arm64 iOS bundle, and records the complete AOT/native pipeline. No claim of device usability yet.

**Device MVP (M9):** the same bundle is signed, installed over USB, and launched on the recorded iPhone. Hello UI and runtime probes succeed across a clean rebuild and a subsequent incremental rebuild. Direct backend sign/deploy commands are acceptable at this research stage; document them explicitly.

**Usable compatibility preview:** M9 plus reproducible bootstrap, validated workload install/list/restore/repair/uninstall, safe rollback, receipt-bound host-root agreement, stock uno-check checking iOS correctly, stock Rider evaluation/build, and an unchanged minimal Uno project running on the device. Standard run-target integration is reported separately until passed. Any missing consumer gate means the release is labeled a narrower CLI/device proof.

**Debugging claim (M10):** limited to the native operations actually demonstrated. LLDB attachment does not imply C# debugging, hot reload, or Rider debugger integration.

Every release publishes the exact tested host, SDK/runtime/toolchain, device OS, application scope, resource limitations, and passed gates. No status is inferred from an upstream project's Flutter or Swift success.

## 10. Remaining implementation backlog

1. Publish bounded, sanitized receipts for the completed lifecycle, minimal-build,
   signing/install/manual-launch, and bootstrap gates; connect each status claim
   to a record in the evidence index.
2. Run complete install/doctor/reinstall/uninstall from root `install.sh` in an
   isolated home when the authentic `Xcode_27.xip` is available.
3. Qualify the constrained helper against a disposable package-manager-style
   system root with unrelated workloads; do not use a live workstation root as
   the first qualification target.
4. Add negative deployment cases without exposing credentials or device identifiers.
5. Qualify stock uno-check against the installed `ios` graph.
6. Qualify Rider evaluation/build against the receipt-bound host root and activation environment.
7. Run an unchanged minimal Uno app through build, signing, installation, and device probes.
8. Expand supported resource/native-library cases without broadening claims beyond evidence.
9. Research automated CoreDevice launch only with an operator-approved personalized DDI.

## 11. Source and evidence notes

The original conversation establishes product intent and candidate versions. It
does not establish experimental results for this repository. The lock file and
published receipts, not mutable upstream pages or narrative summaries, are the
evidence-bearing references. Public links below provide upstream context; when a
link informs a durable claim, record its immutable revision or package hash in
the lock and its observed result in a receipt.

- [.NET workload manifest design](https://github.com/dotnet/designs/blob/main/accepted/2020/workloads/workload-manifest.md): manifest concepts; validate implementation details in the pinned SDK.
- [dotnet workload install](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-workload-install): standard installation and version-selection behavior.
- [Mono CMake source](https://github.com/dotnet/runtime/blob/main/src/mono/CMakeLists.txt): compiler host/target investigation starting point.
- [macios releases](https://github.com/dotnet/macios/releases): resolve the requested toolchain against actual release/package evidence before locking it.
- [Uno environment checking](https://platform.uno/docs/articles/uno-check.html) and [manifest documentation](https://platform.uno/docs/articles/external/uno.check/manifests/README.html): custom-manifest integration entry points.

The original planning baseline predated implementation. Private/local repository
evidence reports the pinned compiler build, project-owned SDK import,
synthetic-Xcode discovery, complete direct workload package graph, isolated
lifecycle and real Android/Wasm coexistence, official NuGet signatures, private
build-only dependency license review, the unsigned `HelloIos` M2–M6 gate,
development signing, clean and incremental replacement installation, repeated
manual launch/runtime probes, and cold relaunch. These remain unpublished until
sanitized receipts are added. The complete root bootstrap run with the authentic
archive, negative deployment gates, automated CoreDevice launch, Rider,
uno-check, Uno, and any release-specific binary redistribution compliance bundle
remain outstanding.
