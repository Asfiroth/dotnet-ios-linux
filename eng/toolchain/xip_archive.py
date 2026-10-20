#!/usr/bin/env python3
"""Bounded readers for the xar/pbzx/odc-cpio layers in an Xcode XIP."""

from __future__ import annotations

import lzma
import plistlib
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


XZ_MAGIC = b"\xfd7zXZ\x00"
ODC_HEADER_SIZE = 76
MAX_MEMBER_NAME_SIZE = 4096
MAX_METADATA_SIZE = 4 * 1024 * 1024
MAX_DECOMPRESSED_CHUNK_SIZE = 128 * 1024 * 1024
XCODE_VERSION = re.compile(r"^[^/]+\.app/Contents/version\.plist$")
PLATFORM_VERSION = re.compile(
    r"^[^/]+\.app/Contents/Developer/Platforms/iPhoneOS\.platform/version\.plist$"
)
SDK_SETTINGS = re.compile(
    r"^(?P<root>[^/]+\.app/Contents/Developer/Platforms/iPhoneOS\.platform/Developer/SDKs/"
    r"iPhoneOS(?P<version>[0-9]+(?:\.[0-9]+){1,2})?\.sdk)/SDKSettings\.plist$"
)
SDK_SYSTEM_VERSION = re.compile(
    r"^(?P<root>[^/]+\.app/Contents/Developer/Platforms/iPhoneOS\.platform/Developer/SDKs/"
    r"iPhoneOS(?P<version>[0-9]+(?:\.[0-9]+){1,2})?\.sdk)/"
    r"System/Library/CoreServices/SystemVersion\.plist$"
)


@dataclass(frozen=True)
class CpioEntry:
    name: str
    device: int
    inode: int
    mode: int
    user_id: int
    group_id: int
    link_count: int
    device_type: int
    modified_time: int
    size: int

    @property
    def hardlink_key(self) -> tuple[int, int]:
        return self.device, self.inode


@dataclass(frozen=True)
class XcodeMetadata:
    xcode_version: str
    xcode_build: str
    platform_version: str
    platform_build: str
    sdk_version: str
    sdk_build: str
    sdk_build_source: str
    sdk_canonical_name: str
    sdk_archive_root: str


def content_range(stream: BinaryIO) -> tuple[int, int]:
    header = stream.read(28)
    if len(header) != 28:
        raise ValueError("not a complete xar header")
    magic, header_size, _version, compressed_size, _uncompressed_size, _algorithm = struct.unpack(
        ">4sHHQQI", header
    )
    if magic != b"xar!":
        raise ValueError("archive does not have xar magic")
    stream.seek(header_size)
    compressed_toc = stream.read(compressed_size)
    if len(compressed_toc) != compressed_size:
        raise ValueError("truncated xar table of contents")
    try:
        root = ET.fromstring(zlib.decompress(compressed_toc))
    except (ET.ParseError, zlib.error) as error:
        raise ValueError("invalid xar table of contents") from error
    toc = root.find("toc")
    if toc is not None:
        for entry in toc.findall("file"):
            if entry.findtext("name") != "Content":
                continue
            try:
                offset = int(entry.findtext("data/offset", ""))
                length = int(entry.findtext("data/length", ""))
            except ValueError as error:
                raise ValueError("invalid XIP Content range") from error
            if offset < 0 or length <= 0:
                raise ValueError("invalid XIP Content range")
            return header_size + compressed_size + offset, length
    raise ValueError("XIP has no Content member")


def pbzx_chunks(stream: BinaryIO, start: int, length: int) -> Iterator[bytes]:
    stream.seek(start)
    if stream.read(4) != b"pbzx":
        raise ValueError("XIP Content is not a pbzx stream")
    if len(stream.read(8)) != 8:
        raise ValueError("truncated pbzx header")
    consumed = 12
    while consumed < length:
        if consumed + 16 > length:
            raise ValueError("truncated pbzx chunk header")
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError("truncated pbzx chunk header")
        _flags, size = struct.unpack(">QQ", header)
        if size > length - consumed - 16:
            raise ValueError("pbzx chunk exceeds the XIP Content member")
        data = stream.read(size)
        if len(data) != size:
            raise ValueError("truncated pbzx chunk")
        consumed += 16 + size
        if not data.startswith(XZ_MAGIC):
            if len(data) > MAX_DECOMPRESSED_CHUNK_SIZE:
                raise ValueError("uncompressed pbzx chunk exceeds the size limit")
            yield data
            continue

        decompressor = lzma.LZMADecompressor()
        try:
            expanded = decompressor.decompress(data, max_length=MAX_DECOMPRESSED_CHUNK_SIZE + 1)
        except lzma.LZMAError as error:
            raise ValueError("invalid XZ stream in pbzx chunk") from error
        if len(expanded) > MAX_DECOMPRESSED_CHUNK_SIZE:
            raise ValueError("decompressed pbzx chunk exceeds the size limit")
        if not decompressor.eof or decompressor.unused_data:
            raise ValueError("invalid XZ stream in pbzx chunk")
        yield expanded
    if consumed != length:
        raise ValueError("pbzx chunks do not fill the XIP Content member")


class ChunkStream:
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._chunk = b""
        self._position = 0

    def _fill(self) -> None:
        while self._position >= len(self._chunk):
            try:
                self._chunk = next(self._chunks)
            except StopIteration as error:
                raise EOFError("truncated cpio stream") from error
            self._position = 0

    def read(self, size: int) -> bytes:
        parts: list[bytes] = []
        while size:
            self._fill()
            part = self._chunk[self._position : self._position + size]
            self._position += len(part)
            size -= len(part)
            parts.append(part)
        return b"".join(parts)

    def skip(self, size: int) -> None:
        while size:
            self._fill()
            step = min(size, len(self._chunk) - self._position)
            self._position += step
            size -= step


class CpioReader:
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._stream = ChunkStream(chunks)
        self._remaining = 0
        self.finished = False

    @property
    def remaining(self) -> int:
        return self._remaining

    def next_entry(self) -> CpioEntry | None:
        if self._remaining:
            raise ValueError("previous cpio member data was not consumed")
        if self.finished:
            return None
        header = self._stream.read(ODC_HEADER_SIZE)
        if header[:6] != b"070707":
            raise ValueError("XIP payload is not an odc cpio archive")
        try:
            values = [
                int(header[6:12], 8),
                int(header[12:18], 8),
                int(header[18:24], 8),
                int(header[24:30], 8),
                int(header[30:36], 8),
                int(header[36:42], 8),
                int(header[42:48], 8),
                int(header[48:59], 8),
                int(header[59:65], 8),
                int(header[65:76], 8),
            ]
        except ValueError as error:
            raise ValueError("invalid cpio header field") from error
        name_size = values[8]
        if not 0 < name_size <= MAX_MEMBER_NAME_SIZE:
            raise ValueError("invalid cpio member name size")
        raw_name = self._stream.read(name_size)
        if raw_name[-1:] != b"\0":
            raise ValueError("cpio member name is not null-terminated")
        name = raw_name[:-1].decode("utf-8", "strict").removeprefix("./")
        if name == "TRAILER!!!":
            self.finished = True
            return None
        self._remaining = values[9]
        return CpioEntry(
            name=name,
            device=values[0],
            inode=values[1],
            mode=values[2],
            user_id=values[3],
            group_id=values[4],
            link_count=values[5],
            device_type=values[6],
            modified_time=values[7],
            size=values[9],
        )

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            size = self._remaining
        if size < 0 or size > self._remaining:
            raise ValueError("read exceeds the current cpio member")
        data = self._stream.read(size)
        self._remaining -= size
        return data

    def skip(self) -> None:
        self._stream.skip(self._remaining)
        self._remaining = 0


def open_cpio(archive: Path) -> Iterator[tuple[CpioEntry, CpioReader]]:
    with archive.open("rb") as stream:
        start, length = content_range(stream)
        reader = CpioReader(pbzx_chunks(stream, start, length))
        while (entry := reader.next_entry()) is not None:
            yield entry, reader
            reader.skip()


def plist_member(reader: CpioReader, entry: CpioEntry, label: str) -> dict:
    if entry.size > MAX_METADATA_SIZE:
        raise ValueError(f"{label} exceeds the metadata size limit")
    try:
        value = plistlib.loads(reader.read())
    except plistlib.InvalidFileException as error:
        raise ValueError(f"{label} is not a plist") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} plist root is not a dictionary")
    return value


def required_string(value: dict, key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{label} lacks {key}")
    return result


def inspect_metadata(archive: Path) -> XcodeMetadata:
    found: dict[str, dict] = {}
    sdk_path_version: str | None = None
    sdk_root: str | None = None
    for entry, reader in open_cpio(archive):
        label = ""
        match = None
        if XCODE_VERSION.fullmatch(entry.name):
            label = "xcode"
        elif PLATFORM_VERSION.fullmatch(entry.name):
            label = "platform"
        elif match := SDK_SETTINGS.fullmatch(entry.name):
            label = "sdk"
        elif match := SDK_SYSTEM_VERSION.fullmatch(entry.name):
            label = "sdk-system"
        if not label:
            continue
        if label in found:
            raise ValueError(f"duplicate {label} metadata member")
        if match:
            path_version = match.group("version")
            path_root = match.group("root")
            if sdk_path_version and path_version and sdk_path_version != path_version:
                raise ValueError("SDK metadata members use different versioned paths")
            if sdk_root and sdk_root != path_root:
                raise ValueError("SDK metadata members use different archive roots")
            sdk_path_version = sdk_path_version or path_version
            sdk_root = path_root
        found[label] = plist_member(reader, entry, label)
        if len(found) == 4:
            break

    missing = {"xcode", "platform", "sdk", "sdk-system"} - found.keys()
    if missing:
        raise ValueError("missing metadata members: " + ", ".join(sorted(missing)))
    if sdk_root is None:
        raise ValueError("cannot determine the SDK archive root")

    xcode = found["xcode"]
    platform = found["platform"]
    sdk = found["sdk"]
    sdk_system = found["sdk-system"]
    sdk_version = required_string(sdk, "Version", "SDKSettings.plist")
    sdk_build = required_string(sdk_system, "ProductBuildVersion", "SystemVersion.plist")
    settings_build = sdk.get("ProductBuildVersion")
    if settings_build is not None and settings_build != sdk_build:
        raise ValueError("SDKSettings.plist and SystemVersion.plist build identifiers do not agree")
    canonical_name = required_string(sdk, "CanonicalName", "SDKSettings.plist")
    if sdk_path_version is not None and sdk_version != sdk_path_version:
        raise ValueError("SDK path and Version do not agree")
    if canonical_name != f"iphoneos{sdk_version}":
        raise ValueError("SDK Version and CanonicalName do not agree")

    return XcodeMetadata(
        xcode_version=required_string(xcode, "CFBundleShortVersionString", "Xcode version.plist"),
        xcode_build=required_string(xcode, "ProductBuildVersion", "Xcode version.plist"),
        platform_version=required_string(
            platform, "CFBundleShortVersionString", "platform version.plist"
        ),
        platform_build=required_string(
            platform, "ProductBuildVersion", "platform version.plist"
        ),
        sdk_version=sdk_version,
        sdk_build=sdk_build,
        sdk_build_source="System/Library/CoreServices/SystemVersion.plist",
        sdk_canonical_name=canonical_name,
        sdk_archive_root=sdk_root,
    )
