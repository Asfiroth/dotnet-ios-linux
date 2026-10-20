# Installation and Device Troubleshooting

This runbook is for operators using the experimental Linux iOS toolchain. It
does not expand the supported scope: the current status page remains the source
of truth for demonstrated capabilities.

## First response

From the repository root, activate the installed environment and run doctor:

```sh
source "${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux/activate"
./install.sh doctor
"$DOTNET_ROOT/dotnet" --info
"$DOTNET_ROOT/dotnet" workload list
```

Keep the output local until it has been reviewed for paths, identifiers, and
secrets. Do not hand-edit workload records, packs, SDK manifests, or installer
receipts to bypass a failure.

## Installation fails before completion

1. Check the compatibility error emitted by the installer. The supplied Xcode
   major/minor must match `RecommendedXcodeVersion` from the selected iOS SDK
   workload pack; for example, a `27.0` workload does not accept Xcode `27.1`.
2. Re-run the same command with the original archive path, not an extracted `Xcode.app`.
   A top-level installation deliberately requires the original XIP layout.
3. Run `./install.sh doctor` only after a completed install. For an interrupted
   transaction, retain the local diagnostics and re-run the supported install
   command; do not delete files under `~/.dotnet` opportunistically.
4. If using `--offline`, confirm that every pinned archive and source checkout
   is already in the project cache. Offline mode intentionally fails rather than
   downloading or substituting versions.

The installer may reuse a newer installed SDK patch within the supported feature
band. It does not cross feature bands. It also selects only an installed workload
set whose iOS mapping matches the locked runtime/AOT graph; a newer incompatible
workload set is not silently substituted.

## `dotnet` or MSBuild selects the wrong SDK

1. Source the generated activation file in the shell that runs the command.
2. Confirm `DOTNET_ROOT` resolves to `~/.dotnet` and `DEVELOPER_DIR` resolves to
   the installed synthetic Xcode developer directory.
3. Remove stale `MSBuildSDKsPath` or architecture-specific `DOTNET_ROOT_*`
   overrides from the invoking environment instead of adding a second SDK root.
4. Run `./install.sh doctor` and retain a sanitized copy of the failure details.

The installer intentionally leaves `/usr/share/dotnet` untouched. A system
`dotnet` on `PATH` is not evidence that the Linux iOS workload is active.

## Signing or installation fails

Use the direct procedures in
[`eng/toolchain/README.md`](../../eng/toolchain/README.md#development-signing-and-device-actions).

- Keep P12 files, password files, profiles, pairing records, and signing JSON
  outside the repository with restrictive permissions.
- Confirm the profile, team, bundle ID, certificate, and connected device are
  mutually authorized before attempting to sign.
- Treat a signature failure, installation failure, and launch failure as three
  separate results. Do not replace the unsigned bundle in place.

## Device is unavailable or launch fails

1. Ensure the phone is unlocked, trusted, connected by USB, and visible to the
   host's `usbmuxd` service.
2. Run the identifier-redacting discovery and lockdown probes described in the
   [toolchain guide](../../eng/toolchain/README.md#pinned-xcode-27-backend).
3. Do not enable automatic pairing as a workaround. The deployment path requires
   an existing pairing and deliberately uses `autopair=False`.
4. Automated CoreDevice launch additionally requires an operator-approved,
   mounted personalized Developer Disk Image. It remains an open gate. Manual
   launch is the demonstrated path; installation alone is not a successful
   launch result.

## IDE, Uno, and debugging expectations

Rider evaluation/build, stock `uno-check`, minimal Uno device execution, and
native debugging remain separate unpassed gates. Use the activated CLI workflow
as the diagnostic baseline. If experimenting with Rider, it must select
`~/.dotnet/dotnet` and inherit the activation environment; do not represent that
configuration as supported until its consumer receipt is published.

## Reporting a problem safely

Include the toolchain-lock hash, sanitized command and exit code, expected and
observed behavior, and the failed boundary. Exclude Apple archive paths,
credentials, profile selectors, device names/UDIDs, pairing data, raw binlogs,
and raw logs. Use the [evidence receipt protocol](../evidence/receipt-schema.md)
when a result needs to become durable project evidence.
