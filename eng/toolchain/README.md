# Synthetic Xcode slice

The supported orchestration entry point is the repository-root `install.sh`.
It validates the complete lock, imports the operator-supplied Xcode archive,
stages these tools under the durable data root, and leaves downloads/builds in
the disposable cache. The commands below document and test individual
components; they are not a second installation layout.

`import-xcode-sdk.py` transactionally imports an authentic device SDK from a
user-supplied Xcode XIP. `build-synthetic-xcode.py` then validates that SDK and creates only
the Xcode filesystem and query contracts consumed by the pinned macios SDK. It
does not emulate Xcode and it does not acquire or redistribute Apple files.

The pinned archive is Xcode `27.0 (27A266a)`, with iPhoneOS platform and SDK
build `24A430`. Import it without extracting the complete Xcode application:

```sh
python3 eng/toolchain/import-xcode-sdk.py \
  --archive /path/to/Xcode_27.xip \
  --archive-sha256 6a270c53a5a0c5e0ac78125342d44c3cfff2716ec390373cd5e30834d80a67c3 \
  --output /path/to/sdk/iPhoneOS27.0.sdk \
  --xcode-version 27.0 \
  --xcode-build 27A266a \
  --platform-version 27.0 \
  --platform-build 24A430 \
  --sdk-version 27.0 \
  --sdk-build 24A430
```

The importer hashes the archive before and after extraction, inspects bounded
metadata before publication, and uses a two-pass cpio walk. Its hardlink logic
matches pinned `unxip` 3.3.0 semantics: the first regular `(device,inode)`
member is authoritative, while later `NULLcanary` members become hardlinks.
It never patches Apple metadata. Replacement requires both `--replace` and the
project ownership marker.

The platform build must come from the source Xcode installation's
`iPhoneOS.platform/version.plist`; it is required rather than invented:

```sh
python3 eng/toolchain/build-synthetic-xcode.py \
  --sdk /path/to/iPhoneOS27.0.sdk \
  --sdk-version 27.0 \
  --xcode-version 27.0 \
  --xcode-build 27A266a \
  --platform-build 24A430 \
  --output "$HOME/.local/share/dotnet-ios-linux/Xcode.app" \
  --activation "$HOME/.local/share/dotnet-ios-linux/activate"
```

The default is a symlink to the source SDK. Use `--mode copy` for a self-contained
local installation. Source the generated activation file before building. The
SDK's own build comes from `SDKSettings.plist/ProductBuildVersion`, or from
`System/Library/CoreServices/SystemVersion.plist/ProductBuildVersion` when the
former is absent as it is in Xcode 27. If both exist, they must agree. This is
validated separately from the required iPhoneOS platform build. The shims
reject simulator SDKs, non-arm64 targets, unknown tools, and unsupported
`xcodebuild` operations. The Linux-only `mdimport` contract validates macios's
output directory and explicitly skips unavailable Spotlight indexing.

## Pinned host toolchain

The project installation path is implemented and audited only from this
repository. Its host tools are independently pinned under
`components.hostToolchain` in `toolchain.lock.json`; external host-setup
projects are neither acquisition inputs nor compatibility or audit authorities.
The selected Xcode 27.0 / iPhoneOS 27.0 family uses the locked Swift 6.4.0
toolchain.

Stage that exact Swift toolchain without changing `/usr`:

```sh
eng/toolchain/setup-swift.sh \
  --destination "$HOME/.local/share/dotnet-ios-linux/manual/swift-6.4.0-pinned" \
  --cache "$HOME/.cache/dotnet-ios-linux/downloads"
```

The setup extracts the hash-verified Swift archive, then builds ncurses `6.6`
and libxml2 `2.13.9` from hash-verified upstream sources with the archive's
bundled Clang 21. The deterministic compatibility libraries are installed only
in Swift's private runtime directories. No Arch package, distro package
manager, `patchelf`, `/usr` mutation, or ambient `LD_LIBRARY_PATH` is used. Two
independent source builds produced byte-identical compatibility libraries and
both passed a complete release `swift build` smoke test.

Stage the exact private normalizer toolchain against that Swift stage:

```sh
eng/toolchain/setup-binutils.sh \
  --destination "$HOME/.local/share/dotnet-ios-linux/manual/binutils-2.47" \
  --cache "$HOME/.cache/dotnet-ios-linux/downloads" \
  --swift-root "$HOME/.local/share/dotnet-ios-linux/manual/swift-6.4.0-pinned"
```

The setup builds GNU Binutils `2.47` from its hash-verified source archive with
the locked Swift Clang/LLD tools. It stages only hash-locked `objcopy`, `strip`,
and `readelf` plus the GPLv3 license. Two builds from distinct source, build,
destination, and Swift prefixes produced identical tool bytes. Ambient GNU or
LLVM normalizers are not accepted as fallbacks.

Build the selected xtool fork against that stage:

```sh
eng/toolchain/setup-xtool.sh \
  --source-cache "$HOME/.cache/dotnet-ios-linux/sources/xtool-f0a1f90" \
  --swift-root "$HOME/.local/share/dotnet-ios-linux/manual/swift-6.4.0-pinned" \
  --binutils-root "$HOME/.local/share/dotnet-ios-linux/manual/binutils-2.47" \
  --destination "$HOME/.local/share/dotnet-ios-linux/manual/xtool-f0a1f90"
```

The build enforces `eng/toolchain/xtool-Package.resolved.lock`, including the
`OpenCombine` transitive pin missing from the upstream resolution, and verifies
XADI's source revision and binary-artifact checksum. It leaves the source cache
clean and bundles only the required private Swift runtime libraries with an
`$ORIGIN` runpath. The setup creates the neutral host mountpoint
`/tmp/dotnet-ios-linux` when absent. A rootless user/mount namespace overlays
only that directory with private tmpfs and presents every physical scratch tree
at one fixed logical child path before compilation, preventing physical SwiftPM
prefixes from entering Swift `#file` strings. No OpenCode-created path or
pre-existing logical child is required. The pinned GNU tools then remove the
non-runtime build-ID note, zero `.swift_modhash`, and strip unneeded symbols.
Fresh offline builds from different physical and Swift prefixes are
byte-identical at xtool SHA-256
`c13e2270e6e9d9cce21ba7daae1b51016fcbd50c1bfd2c6747b1ebd208cb168d`
and libXADI SHA-256
`eb68bd3dad62f68eac166aad7dc4e8c931013f42a529a8f5d4c520e671375350`.
Offline reuse rechecks those hashes and executes `--help` with
`LD_LIBRARY_PATH` unset. The private stage also retains all 55 dependency
license files and 18 notices under `share/licenses/xtool`; its deterministic
74-entry checksum inventory is rechecked on reuse.

`--diagnostics-dir <new-directory>` may be supplied for a failed build. A hash
mismatch retains checksummed phase artifacts, static ELF metadata, and exact
normalizer provenance atomically. It never executes the unverified xtool;
runtime checks occur only after the complete output hashes pass.

Stage the pinned portable Python and uv before deployment tooling:

```sh
eng/toolchain/setup-python.sh \
  --destination "$HOME/.local/share/dotnet-ios-linux/manual/python-3.14.8" \
  --cache "$HOME/.cache/dotnet-ios-linux/downloads"
```

This uses CPython `3.14.8` build `20261013` and uv `0.9.10`; both archives and
their staged executables are hash-verified. The stage retains the CPython
license, python-build-standalone's MPL license and consolidated bundled-library
license inventory, and uv's Apache/MIT licenses under `share/licenses`. Those
files are independently pinned and remain available after cache deletion. It
does not merge into a system Python.

Device transport uses independently pinned `pymobiledevice3` 11.26.0. Build it
transactionally in a project-owned prefix with the complete Python
3.14/x86-64 lock:

```sh
eng/toolchain/setup-pymobiledevice3.sh \
  --destination "$HOME/.local/share/dotnet-ios-linux/manual/pymobiledevice3-11.26" \
  --python "$HOME/.local/share/dotnet-ios-linux/manual/python-3.14.8/bin/python3" \
  --uv "$HOME/.local/share/dotnet-ios-linux/manual/python-3.14.8/bin/uv" \
  --cache "$HOME/.cache/dotnet-ios-linux/pymobiledevice3"
```

This environment contains no authentication or pairing records. Keep those in
their normal private user locations and never include them in receipts. Full
native-link mode still requires a user-supplied Xcode 27 archive or extracted
SDK; partial framework collections are not accepted for that gate.

USB transport requires a working host `usbmuxd` service and OS device access.
Provision those with the host's normal administration mechanism; the project
installer does not elevate privileges or modify system services. After
reconnecting the unlocked phone, run identifier-free discovery first:

```sh
"$HOME/.local/share/dotnet-ios-linux/manual/pymobiledevice3-11.26/bin/python" \
  eng/toolchain/check-device-transport.py
```

Only after the phone has been trusted may the same probe validate an existing
lockdown pairing:

```sh
"$HOME/.local/share/dotnet-ios-linux/manual/pymobiledevice3-11.26/bin/python" \
  eng/toolchain/check-device-transport.py --lockdown
```

The probe calls the pinned API directly, disables library logging, reports only
counts and exception class names, never auto-pairs, and uses a temporary private
pairing cache. Actual pairing remains a separate explicit interactive action.

xtool's native `devices` output includes the device name and UDID. Use the
redacting wrapper instead of invoking that command directly:

```sh
python3 eng/toolchain/check-xtool-devices.py \
  --xtool-root "$HOME/.local/share/dotnet-ios-linux/manual/xtool-f0a1f90"
```

The wrapper verifies the stage marker and executable hash, captures native
output only in memory, and emits only a USB-device count and status.

## rcodesign

Stage the exact Linux x86-64 `rcodesign` release in a project-owned prefix:

```sh
eng/toolchain/setup-rcodesign.sh \
  --cache /path/to/download-cache \
  --destination /path/to/tools/rcodesign-0.29.0
```

The setup verifies the release archive, its exact three-member layout, the
executable and license-inventory hashes, ELF architecture, static-PIE linkage,
and the exact `apple-codesign 0.29.0` version. Publication is atomic and
no-clobber. An existing destination is reused only when its
ownership/provenance marker and payload still match; corrupt caches and unowned
destinations are not replaced.
The setup does not inspect credentials, create certificates, or sign code.

The staged `COPYING` file inventories 335 transitive components and 13 SPDX
license families. Its one scanner exception, `ring 0.17.8`, was resolved against
the exact checksum in the release `Cargo.lock`; that crate carries its
ISC/BoringSSL-derived license notice. This review permits private build-tool use
for this experiment, with `COPYING` retained beside the executable. It does not
make a redistribution claim.

## Development signing and device actions

Existing local signing assets are consumed through a private JSON file rather
than command-line secret values. Copy `eng/toolchain/signing-config.example.json`
to a private location, fill in the P12, password-file, provisioning-profile,
team, and bundle values, then restrict all four private files. For automatic
runner discovery, use the exact name `ios-signing.json` in the directory from
which the runner will be invoked:

```sh
chmod 600 /private/run-directory/ios-signing.json \
  /private/path/apple-development.p12 \
  /private/path/p12-password.txt \
  /private/path/development.mobileprovision
```

The password file may end in a newline; the signer normalizes it into an
ephemeral mode-600 file. It never places the password itself in an argument.
Sign from the canonical unsigned output into a new path:

```sh
eng/toolchain/sign-ios-app.py \
  --config /private/path/signing.json \
  --app /path/to/unsigned/HelloIos.app \
  --output /path/to/signed/HelloIos.app \
  --rcodesign-root /path/to/rcodesign-0.29.0 \
  --pymobiledevice3-root /path/to/pymobiledevice3-11.26.0
```

The signer verifies the P12 key/certificate pair, profile CMS integrity,
expiry, development entitlement, certificate membership, exact team and bundle
coverage, and authorization of the sole connected USB device. It signs an
ephemeral copy inside-out, disables network timestamping, verifies the resulting
Mach-O signature and CodeResources seal, and atomically publishes a new app.
It never modifies or replaces the unsigned source bundle.

Run device operations with the pinned environment's Python. `preflight` is
read-only and never pairs. Installation and launch are separate operations that
fail unless their explicit confirmation flag is present:

```sh
/path/to/pymobiledevice3-11.26.0/bin/python eng/toolchain/device-ios-app.py \
  preflight --app /path/to/signed/HelloIos.app \
  --rcodesign-root /path/to/rcodesign-0.29.0

/path/to/pymobiledevice3-11.26.0/bin/python eng/toolchain/device-ios-app.py \
  install --confirm-install --app /path/to/signed/HelloIos.app \
  --rcodesign-root /path/to/rcodesign-0.29.0

/path/to/pymobiledevice3-11.26.0/bin/python eng/toolchain/device-ios-app.py \
  launch --confirm-launch --bundle-identifier dev.dotnetioslinux.helloios
```

The deployment wrapper requires exactly one USB device and opens only an
existing lockdown pairing (`autopair=False`). It suppresses library logs and
never emits the device identifier. Launch additionally requires an already
mounted personalized Developer Disk Image and establishes an in-process
userspace tunnel with both lockdown auto-pairing and remote-pairing fallback
disabled. DDI acquisition/mounting remains a separate explicit workflow because
the image is Apple material and must come from an operator-approved source.

For an installed toolchain, the repository-root runner applies the same
boundaries to a compatible application project:

```sh
./run-ios-app.sh \
  --project /absolute/path/to/App.csproj \
  --confirm-install --confirm-launch
```

When `--signing-config` is omitted, the runner checks only
`$PWD/ios-signing.json`; it does not scan the project directory or parent tree.
Pass `--signing-config /private/path/signing.json` to override that default. The
config must remain current-user-owned and mode `600`; do not commit it or any
Apple signing assets. The runner validates the config file and schema before the
build, but defers P12, password-file, and provisioning-profile validation until
the signing step.

It runs `doctor`, requires the project to select the receipt-recorded SDK,
builds only `Release`/`net10.0-ios`/`ios-arm64` into the private project cache,
requires exactly one generated `.app`, signs a copy, and refuses to install when
preflight reports that the personalized Developer Disk Image is not mounted.
The current signing gate rejects app extensions because they require separate
profiles. The runner does not pair a device, acquire or mount Apple material,
or attach a debugger.
