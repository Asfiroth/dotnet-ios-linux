# Linux iOS compatibility SDK

This SDK contains evaluation-time host compatibility properties and is intended
to be imported automatically by the Linux `ios` workload graph.

For macios `27.0.10722`, `DetectSdkLocations` is passed the `XcodeLocation`
property. On Linux it cannot fall back to `/usr/bin/xcode-select`, and this
revision does not map `DEVELOPER_DIR` into that property. `Sdk/Sdk.props`
performs only that mapping, from `.../Xcode.app/Contents/Developer` to the
containing `Xcode.app`. An explicit `XcodeLocation` always wins.
