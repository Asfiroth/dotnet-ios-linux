# AotProbe

This iOS-targeted class library exercises generic code, managed exceptions,
P/Invoke, and a native callback. `HelloIos` references the library and invokes
`DeviceAotProbe.Execute()` during UIKit startup, so the app's AOT/native-link
pipeline includes the probe.

Building this project alone only validates library compilation. The M4 AOT
evidence comes from a HelloIos app build; the M9 gate is device execution with
the rendered probe result. The P/Invoke path is intentionally device-specific
and must not be run on Linux.
