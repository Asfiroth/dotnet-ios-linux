# Compatibility Matrix

Status values are evidence states, not support claims. `candidate` means a
requested input exists; it does not mean it works on Linux.

| Area | Candidate | Evidence state | Required next evidence |
| --- | --- | --- | --- |
| Linux host | glibc Linux x86_64; observed on EndeavourOS/Arch | SDK-free contracts, portable tool stages, isolated canonical-root lifecycle, and device path observed | Broader distro coverage |
| .NET SDK | 10.0.402 / feature band 10.0.400 | archive hash verified; conflict-safe merge and real Android/Wasm coexistence passed | Complete root bootstrap with authentic Xcode archive |
| macios | 27.0.10722 / Xcode 27.0 | Linux SDK discovery, trim, LLVM AOT, native compile/link, bundle, development signing, USB installation, clean/incremental launch, and cold relaunch verified | Broader app coverage |
| Workload set | CLI 10.0.401.1 / package 10.401.1 | private `global.json` pin; update/install/list/restore/repair/uninstall/clean/reinstall and exact-package NuGet signature verification passed | Root bootstrap with authentic Xcode archive |
| iOS workload | `ios` | deterministic unsigned official-ID derivative with complete hash-locked direct Linux graph | Stock uno-check and Rider consumers |
| Mono device AOT | Linux x64 host to iOS arm64 | Runtime commit matched; clean and incremental `HelloIos` builds execute the LLVM compiler, consume both AotProbe objects, and pass repeated physical-device probes | Broader runtime and application coverage |
| Apple SDK | Xcode `27.0 (27A266a)` / iPhoneOS `27.0 (24A430)` | user-supplied XIP hash and authentic SDK metadata verified; transactional SDK-only import passed | Redistribution/legal review remains separate; no SDK payload may ship |
| Darwin backend | Linux Clang/LLD adapter for the minimal slice | synthetic queries, generated source compile, native link, canonical app output, final Mach-O inspection, signed-copy compatibility, and repeated device runtime pass | Broader tool coverage |
| Signing and USB deployment | development identity and paired iPhone | P12/profile/team/bundle/device checks pass; clean/incremental signed bundles verify, install with no auto-pairing, and pass manual launch/cold-relaunch UI and AOT probes | Automated launch DDI and negative cases |
| Uno and Rider | stock consumers | not started | Separate consumer-gate receipts |

## Claim Boundaries

- M0/M1 evidence does not prove a build pipeline.
- M4 AOT compilation does not prove device runtime compatibility.
- M6 proves an unsigned technical bundle only.
- M9 is required for a device MVP claim.
- Uno, Rider, and debugging each require independent evidence.

## M0 Observations

- The superseded .NET SDK `10.0.302` candidate is preserved under receipt-linked
  historical evidence; it is not part of the active baseline.
- The active .NET SDK `10.0.402` hash matches official release metadata and
  runs in an isolated temporary home. Its unmodified, workload-free Linux evaluation reaches workload resolution,
  then reports missing `wasm-tools` rather than an iOS workload. This is an
  observation to classify with a Mac reference trace, not a workaround target.
- The active official iOS manifest package exposes `ios` and Windows SDK aliases,
  but no Linux host mapping. The implemented unsigned local derivative keeps the
  exact official ID/version while defining the hash-locked direct Linux device
  graph; the authentic input's NuGet signatures are verified first.
- The explicit-dotnet Mac captures verify the active SDK/workload/Xcode baseline
  and installed AOT/runtime pack directories. `HelloIos` stopped at
  provisioning-profile discovery before AOT, native linking, or bundle output.
- A standalone `AotProbe` build remains class-library compilation only. A later
  clean HelloIos app build references the library and its verified private
  binlog records `AotProbe.dll` trimming, Mono AOT compilation, generated AOT
  artifacts, and inclusion in the native link. That Mac reference receipt does
  not include device execution.
- The received reports reference binlogs written below the project directories,
  not the supplied capture directories. The hardened capture script now uses an
  absolute private output path, requires the binlog, and returns build failure.
- A later profile-selected HelloIos capture completed successfully after a
  clean. Its verified private binlog records actual `mono-aot-cross` output for
  `arm64-ios`, generated AOT artifacts, an executed `clang++` native-link
  command, and linker output. This proves the Mac reference path only; it does
  not establish Linux-hosted AOT, final-app architecture/signature inspection,
  or device execution.
- Runtime source `4271d88e0aebf3d04f188f1334c2220d80555ef6` builds a Linux
  x86-64 `mono-aot-cross` for `aarch64-apple-darwin10` with the repository patch.
  In explicit non-LLVM feasibility mode it compiles `AotProbe.dll`; Linux Clang
  accepts the result as a Mach-O arm64 relocatable object. This is compiler-smoke
  evidence only, not the executable `HelloIos` M4 gate.
- The matched LLVM SDK and Tools packages are version
  `19.1.0-alpha.1.25461.1`. Their package hashes and source commit are locked.
  The LLVM-enabled compiler reports `LLVM: yes(1900)`, compiles all 3947 probe
  methods, invokes those exact `opt`/`llc` binaries, and emits separate inspected
  Mono and LLVM Mach-O arm64 relocatable objects. This remains compiler-smoke
  evidence, not executable `HelloIos` acceptance; no script silently downgrades
  an LLVM request.
- Against the SDK-free contract fixture, the actual macios
  `_DetectSdkLocations` target resolves the synthetic Xcode through the
  compatibility SDK. `_FindAotCompiler` selects the staged
   `MonoAotCrossCompiler` item for `ios-arm64`. These gates use isolated per-run
   intermediates so macios's compiler-path cache cannot reuse another pack.
- The user-supplied Xcode XIP has SHA-256
  `6a270c53a5a0c5e0ac78125342d44c3cfff2716ec390373cd5e30834d80a67c3`.
  Bounded metadata inspection reports Xcode `27.0 (27A266a)` and iPhoneOS
  platform/SDK `27.0 (24A430)`. The two-pass importer materializes 10,428 SDK
  members and leaves zero `NULLcanary` placeholders without modifying metadata.
- The private unsigned `HelloIos` gate passes through trimming, LLVM AOT,
  generated/native compilation, and native linking. `LinkNativeCode` consumes
  both `AotProbe.dll.o` and `AotProbe.dll.llvm.o`. The canonical app executable
  is Mach-O arm64, has deployment target 15.0 and SDK 27.0, and contains no
  `LC_CODE_SIGNATURE`. The gate also requires the native-to-managed `Compare`
  callback wrapper in the AotProbe object and final executable.
- The pinned `rcodesign 0.29.0` stage signs a private copy only after validating
  the P12 key/certificate pair, development-profile expiry and entitlements,
  exact team/bundle coverage, certificate membership, and authorization of the
  sole USB device. The signed Mach-O retains iOS 15.0 / SDK 27.0 metadata and
  gains `LC_CODE_SIGNATURE` plus a CodeResources seal. Pinned
  `pymobiledevice3 11.26.0` installs it using an existing pairing with
  `autopair=False`, then finds the exact installed bundle. No identifier is
  emitted.
- A complete 166,799-member scan of the hash-verified Xcode XIP finds no DDI
  image, trust cache, or build manifest. Automated CoreDevice launch therefore
  remains unavailable until an operator-approved personalized Developer Disk
  Image is supplied and mounted.
- The pre-fix app aborted during `AppDelegate.FinishedLaunching`; its executable
  lacked the native-to-managed wrapper for the `qsort` comparer. Annotating the
  static callback with `MonoPInvokeCallback` generated that wrapper in both the
  AotProbe object and final executable. After rebuilding, signing, and replacing
  the installed bundle, a manual icon launch stayed open and displayed
  `generic-aot`, exception code 73, a positive process ID, and checksum 1004.
- A subsequent incremental pass re-executed C# compilation, trimming, LLVM AOT,
  native compilation, and native linking without changing source contents. Its
  LLVM object and final executable differed from the clean run, retained the
  callback wrapper, and passed fresh signing and replacement installation. Both
  manual launch and force-quit/cold relaunch displayed the same probe results;
  a redacted read-only observation saw foreground/process activity and no fatal
  report or crash activity. This completes the full M9 positive path.
- The direct Linux workload graph contains 12 hash-locked official/project/feed
  archives. An isolated SDK lifecycle passed update, install, list, restore,
  repair, uninstall, clean, reinstall, and unchanged-project LLVM build. A
  separate disposable copy of the real canonical SDK retained Android,
  `maui-android`, and `wasm-tools` plus their shared packs after installing and
  uninstalling this iOS graph.
- All nine installed official graph archives, the authentic manifest input, and
  the workload-set package pass NuGet author/repository signature verification.
  The derivative and two project packages are intentionally unsigned and are
  verified by deterministic hashes and explicit absence of `.signature.p7s`.
