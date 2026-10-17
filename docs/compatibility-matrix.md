# Compatibility Matrix

Status values are evidence states, not support claims. `candidate` means a
requested input exists; it does not mean it works on Linux.

| Area | Candidate | Evidence state | Required next evidence |
| --- | --- | --- | --- |
| Linux host | EndeavourOS/Arch x86_64, glibc | observed | Full M0 inventory and disposable SDK run |
| .NET SDK | 10.0.302 / feature band 10.0.300 | source availability verified | Download hash verification and reference-Mac parity |
| macios | 26.5.10301 / Xcode 26.6 | release and commit verified | Mac package graph, tasks, and binlog |
| Workload set | CLI 10.0.302.0 / package 10.302.0 | package inspected; declares `10.0.100` manifest suffix | Effective install mode and lifecycle experiments |
| iOS workload | `ios` | official manifest inspected; Linux mapping absent | One effective Linux-compatible definition |
| Mono device AOT | Linux x64 host to iOS arm64 | unverified | Runtime commit, compiler build, object inspection |
| Apple SDK | Xcode 26.6 / iPhoneOS 26.5 family | user-supplied only | Legal review, source metadata, hashes |
| Darwin backend | omarchy-apple-dev components | unverified | Pinned revision and command-contract evidence |
| Signing and USB deployment | development identity and paired iPhone | not started | Trust-boundary policy and private-environment receipts |
| Uno and Rider | stock consumers | not started | Separate consumer-gate receipts |

## Claim Boundaries

- M0/M1 evidence does not prove a build pipeline.
- M4 AOT compilation does not prove device runtime compatibility.
- M6 proves an unsigned technical bundle only.
- M9 is required for a device MVP claim.
- Uno, Rider, and debugging each require independent evidence.

## M0 Observations

- The downloaded .NET SDK `10.0.302` hash matches official release metadata and
  runs in an isolated temporary home.
- Its unmodified, workload-free Linux evaluation reaches workload resolution,
  then reports missing `wasm-tools` rather than an iOS workload. This is an
  observation to classify with a Mac reference trace, not a workaround target.
- The official iOS manifest package exposes `ios` and Windows SDK aliases, but
  no Linux host mapping. The project-owned manifest/replacement decision remains
  unresolved.
