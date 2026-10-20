#!/usr/bin/env python3
"""Stream required Xcode/iPhoneOS metadata from an Apple XIP archive."""

from __future__ import annotations

import argparse
from pathlib import Path

from xip_archive import inspect_metadata


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Xcode, iPhoneOS platform, and device-SDK metadata without extracting the XIP."
    )
    parser.add_argument("archive", type=Path)
    return parser.parse_args()


def main() -> int:
    options = arguments()
    try:
        metadata = inspect_metadata(options.archive)
    except (EOFError, OSError, UnicodeDecodeError, ValueError) as error:
        print(f"XIP metadata inspection failed: {error}")
        return 1

    print(f"xcode_version={metadata.xcode_version}")
    print(f"xcode_build={metadata.xcode_build}")
    print(f"platform_version={metadata.platform_version}")
    print(f"platform_build={metadata.platform_build}")
    print(f"sdk_version={metadata.sdk_version}")
    print(f"sdk_build={metadata.sdk_build}")
    print(f"sdk_build_source={metadata.sdk_build_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
