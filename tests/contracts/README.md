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
detection without using real credentials, profiles, or device identifiers.

Run the complete SDK-free repository validation, including these tests, shell
syntax, lockfile paths, documentation links, and private-artifact guards, with
`eng/evidence/validate-repository.sh`.
