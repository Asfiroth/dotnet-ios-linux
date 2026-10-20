# Product source

This directory contains the compatibility components intended to become part of
the distributable product:

- `msbuild/` — Linux integration imported around the standard .NET build graph;
- `packs/` — project-owned SDK and AOT pack templates, without official or
  generated binaries; and
- `shims/` — fail-closed Linux implementations of the required Xcode command
  entry points.

Acquisition, build, signing, deployment, and evidence tooling belongs under
`eng/`, not here.
