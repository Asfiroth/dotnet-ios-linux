# Tests

- `AotProbe/` — iOS-targeted runtime fixture for generics, managed exceptions,
  P/Invoke, and native callbacks.
- `HelloIos/` — ordinary `net10.0-ios` executable that renders probe results.
- `contracts/` — SDK-free unit and host-contract tests suitable for public CI.
- `integration/` — explicit private gates requiring operator-supplied SDK and
  runtime inputs.

The two application project files remain ordinary portable .NET projects and
must not acquire Linux-only SDK names or project properties.
