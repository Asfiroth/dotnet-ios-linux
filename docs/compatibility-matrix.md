# Compatibility Matrix

Status values are evidence states, not support claims. `candidate` means a
requested input exists; it does not mean it works on Linux.

| Area | Candidate | Evidence state | Required next evidence |
| --- | --- | --- | --- |
| Linux host | EndeavourOS/Arch x86_64, glibc | observed | Full M0 inventory and disposable SDK run |
| .NET SDK | 10.0.402 / feature band 10.0.400 | source availability verified | Download hash verification and reference-Mac parity |
| macios | 27.0.10722 / Xcode 27.0 | release/package commits, Mac inventory, AOT execution, and native link verified | Retained final-app inspection and clean-worktree reference capture |
| Workload set | CLI 10.0.401.1 / package 10.401.1 | package inspected; declares `10.0.100` manifest suffix | Effective install mode and lifecycle experiments |
| iOS workload | `ios` | official manifest inspected; Linux mapping absent | One effective Linux-compatible definition |
| Mono device AOT | Linux x64 host to iOS arm64 | Mac reference AOT/native link verified; Linux host unverified | Linux compiler build, object inspection, and matching runtime provenance |
| Apple SDK | Xcode 27.0 / iPhoneOS 27.0 family | user-supplied only | Legal review, source metadata, hashes |
| Darwin backend | omarchy-apple-dev components | unverified | Pinned revision and command-contract evidence |
| Signing and USB deployment | development identity and paired iPhone | profile selection and successful Mac build observed; signature verification/USB deployment not started | Final-app signature inspection, installation, and launch receipts |
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
- The active official iOS manifest package exposes `ios` and Windows SDK aliases, but
  no Linux host mapping. The project-owned manifest/replacement decision remains
  unresolved.
- The explicit-dotnet Mac captures verify the active SDK/workload/Xcode baseline
  and installed AOT/runtime pack directories. `HelloIos` stopped at
  provisioning-profile discovery before AOT, native linking, or bundle output.
- `AotProbe` compiled as an iOS-targeted class library and produced only managed
  `AotProbe.dll`; it is not an AOT, native-output, or device-execution result.
- The received reports reference binlogs written below the project directories,
  not the supplied capture directories. The hardened capture script now uses an
  absolute private output path, requires the binlog, and returns build failure.
- A later profile-selected HelloIos capture completed successfully after a
  clean. Its verified private binlog records actual `mono-aot-cross` output for
  `arm64-ios`, generated AOT artifacts, an executed `clang++` native-link
  command, and linker output. This proves the Mac reference path only; it does
  not establish Linux-hosted AOT, final-app architecture/signature inspection,
  or device execution.
