# ADR-0006: Use the active host .NET root with a constrained privilege boundary

**Status:** Accepted
**Date:** 2026-10-21
**Owner:** Repository maintainer
**Supersedes:** [ADR-0001](0001-canonical-dotnet-root.md)

## Context

The installer previously fixed the participating SDK/workload root at
`~/.dotnet`. On general Linux systems, the active `dotnet` command commonly
resolves to a package-manager installation such as `/usr/share/dotnet`. Requiring
a second SDK root makes CLI and IDE selection less predictable, but running the
complete installer with `sudo` would expose downloads, builds, user paths, and
rollback code to unnecessary privilege.

## Decision

Resolve the participating root from the real path of `command -v dotnet` and use
the directory containing that executable as `DOTNET_ROOT`.

- A writable host root uses the existing unprivileged SDK/workload lifecycle.
- A non-writable root must already contain a compatible SDK. The installer does
  not merge a fallback SDK into that root. It requires the lock's exact
  workload-set version; when absent, that locked package is supplied through the
  helper's verified local-only workload bundle rather than selecting another
  installed set.
- The installer asks before acquiring elevation and never runs itself as root.
- All workload archives are downloaded, signature/hash checked, and assembled
  into a complete local-only bundle before elevation.
- An immutable, content-addressed root-owned helper under
  `/usr/local/lib/dotnet-ios-linux/versions` is the only project code that
  invokes `dotnet` with workload-mutation privilege. Its matching trust lock is
  under `/usr/local/share/dotnet-ios-linux/versions`.
- Privileged state is root-owned under `/var/lib/dotnet-ios-linux`; root-scoped
  locks are under `/run/lock/dotnet-ios-linux`.
- Privileged child commands receive an allowlisted environment and a new network
  namespace. Installation fails closed if network isolation cannot be created;
  each child inherits the root lifecycle lock until it exits.
- A random ownership token binds the user receipt to privileged verify,
  recovery, and uninstall operations.
- Workload restoration uses durable operation cursors and exact semantic
  ownership. It locks `globaljsonworkloadsets.json` with the same Linux `flock`
  primitive used by SDK `FileShare.None`, publishes the owned key change last,
  and retains exact resources when another recorded key selects the same set.

The constrained first-run bootstrap uses validated root-owned host utilities to
publish the helper and trust files. Subsequent privileged lifecycle execution
invokes the installed helper directly through its fixed isolated-Python shebang.

## Alternatives rejected

- Always use `~/.dotnet`, because it can diverge from the host CLI and IDE SDK.
- Run `sudo ./install.sh`, because that elevates acquisition, build, and
  user-controlled filesystem work.
- Let privileged `dotnet workload` download packages, because the privileged
  network and package trust boundary would be wider and less auditable.
- Install a fallback SDK into a non-writable root, because package-manager SDK
  ownership and rollback cannot be inferred safely.

## Consequences

- A host `dotnet` command is now a prerequisite.
- User-local roots preserve the previous merge and coexistence behavior.
- Package-manager roots require explicit authorization and a compatible
  pre-existing SDK.
- System-root support remains a candidate until it passes a disposable live-root
  qualification with unrelated workloads present; repository contracts do not
  establish distribution-wide support.
- Activation, doctor, and `run-ios-app.sh` use the receipt-bound host root rather
  than assuming `~/.dotnet`.

## Evidence boundary and review

SDK-free contracts cover root discovery, privilege selection, sealed-bundle
integrity, ownership-token enforcement, rollback state, exact workload identity,
interruption replay, child lock inheritance, and shared-resource retention. A
live `/usr/share/dotnet` mutation is not part of
that evidence and requires a separate disposable-host gate. Revisit this decision
if a distribution's .NET layout cannot satisfy the root ownership, isolated
network, or workload coexistence checks.
