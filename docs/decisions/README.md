# Architecture Decision Records

These records preserve accepted architectural choices that otherwise become
buried in planning discussions or status prose. They explain *why* the project
uses a boundary; the [specification](../specification.md) defines the wider
system, and the [compatibility matrix](../status/compatibility-matrix.md) states
what has actually been demonstrated.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-canonical-dotnet-root.md) | Use one canonical user-local .NET root | Superseded by 0006 |
| [0002](0002-standard-ios-workload-identity.md) | Preserve the public `ios` workload identity | Accepted |
| [0003](0003-synthetic-xcode-through-developer-dir.md) | Select the synthetic Xcode layout through `DEVELOPER_DIR` | Accepted |
| [0004](0004-linux-hosted-mono-llvm-aot.md) | Use a matched Linux-hosted Mono LLVM AOT compiler | Accepted |
| [0005](0005-explicit-signing-and-deployment-boundary.md) | Keep signing, deployment, and launch explicit | Accepted |
| [0006](0006-use-the-active-host-dotnet-root.md) | Use the active host .NET root with a constrained privilege boundary | Accepted |

New records should identify an owner, state the context, alternatives rejected,
decision, consequences, evidence boundary, and review trigger. They must not
turn an unpublished observation into a support claim.
