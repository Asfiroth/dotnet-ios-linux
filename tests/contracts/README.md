# Host contract tests

Run the SDK-free synthetic-Xcode and shim contracts with:

```sh
python3 -m unittest discover -s tests/contracts -p 'test_*.py' -v
```

The tests use a deliberately incomplete fake SDK contract (metadata, UIKit
directory, and `libSystem.tbd`) and fake Clang backends. They verify discovery,
argument boundaries, Linux driver adaptation, and fail-closed behavior; they do
not claim Apple SDK or linker integration. The suite also tests development
profile team/bundle constraints and Mach-O code-signature load-command
detection without using real credentials, profiles, or device identifiers. The
device-runner contracts verify arbitrary application bundle identifiers, the
documented no-debug boundary, and explicit install confirmation.
Installer contracts use synthetic .NET archives and workload trees to exercise
merge conflicts, interruption recovery, rollback preflight, ownership-aware
uninstall, coexistence with later workloads, and mutation-free dry runs without
downloading or executing an SDK. They also exercise randomized verified
downloads, cache ownership, and Linux atomic no-clobber directory publication.
System-root contracts cover host-executable symlink resolution, privilege-mode
selection, deterministic complete offline bundles, traversal and tamper
rejection, sanitized privileged environments, required network isolation,
receipt-bound ownership tokens, inherited lifecycle locks, fsync-backed replay,
same-workload-set resource retention, resumable uninstall, and root-owned
transaction state. They use temporary fake roots and never invoke real `sudo`
or mutate a live package-manager installation.

Run the complete SDK-free repository validation, including these tests, shell
syntax, lockfile paths, documentation links, and private-artifact guards, with
`eng/evidence/validate-repository.sh`.
