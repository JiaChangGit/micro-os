#!/usr/bin/env python3
"""驗證固定 AArch64 ELF，產生核心使用的 segment descriptor。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct

PAGE = 4096
IMAGE_START = 0x00400000
IMAGE_END = 0x00600000
PAYLOAD_BYTES = 0x20000 - 5 * PAGE  # BootInfo 與四頁 stack 不屬於 ELF payload。
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")


@dataclass(frozen=True)
class Segment:
    va: int
    mem_size: int
    flags: int
    data: bytes


@dataclass(frozen=True)
class Image:
    entry: int
    segments: tuple[Segment, ...]


def read_image(data: bytes) -> Image:
    if len(data) < ELF_HEADER.size:
        raise ValueError("truncated ELF header")
    (ident, kind, machine, version, entry, phoff, _shoff, _flags,
     ehsize, phentsize, phnum, _shentsize, _shnum, _shstrndx) = ELF_HEADER.unpack_from(data)
    if ident[:7] != b"\x7fELF\x02\x01\x01" or kind != 2 or machine != 183 or version != 1:
        raise ValueError("expected little-endian AArch64 ELF64 executable")
    if ehsize != ELF_HEADER.size or phentsize != PROGRAM_HEADER.size or not 1 <= phnum <= 16:
        raise ValueError("unsupported ELF header sizes")
    if phoff < ehsize or phoff + phnum * phentsize > len(data):
        raise ValueError("program headers outside file")
    segments = []
    occupied = []
    for index in range(phnum):
        tag, flags, offset, va, pa, filesz, memsz, align = PROGRAM_HEADER.unpack_from(
            data, phoff + index * phentsize)
        if tag in (2, 3, 7):
            raise ValueError("dynamic linking, interpreter and TLS are unsupported")
        if tag != 1:
            continue
        if not memsz:
            if filesz:
                raise ValueError("file bytes without memory")
            continue
        if flags & ~7 or not flags & 4 or flags & 3 == 3:
            raise ValueError("invalid segment permissions or W+X")
        if align != PAGE or va % PAGE or offset % PAGE or pa != va:
            raise ValueError("unaligned or relocated segment")
        if filesz > memsz or offset + filesz > len(data):
            raise ValueError("segment outside file")
        end = (va + memsz + PAGE - 1) & -PAGE
        if va < IMAGE_START or end > IMAGE_END or end <= va:
            raise ValueError("segment outside fixed image slot")
        if any(va < other_end and other_start < end for other_start, other_end in occupied):
            raise ValueError("overlapping segment pages")
        occupied.append((va, end))
        segments.append(Segment(va, memsz, flags, data[offset:offset + filesz]))
    if not 1 <= len(segments) <= 4:
        raise ValueError("unsupported segment count")
    if sum((s.mem_size + PAGE - 1) & -PAGE for s in segments) > PAYLOAD_BYTES:
        raise ValueError("segments exceed fixed physical frame reservation")
    if entry % 4 or not any(s.flags & 1 and s.va <= entry and entry + 4 <= s.va + len(s.data) for s in segments):
        raise ValueError("entry is outside executable file bytes")
    return Image(entry, tuple(segments))


def render(images: list[Image]) -> str:
    if len(images) != 3:
        raise ValueError("expected root, rtc and client images")
    lines = ['/* 固定映像的建置產物；由 tools/pack_users.py 產生。 */',
             '#ifndef MICRO_USER_IMAGES_GENERATED_H', '#define MICRO_USER_IMAGES_GENERATED_H',
             '#include "user_image.h"']
    for role, image in enumerate(images):
        for index, segment in enumerate(image.segments):
            lines.append(f'static const unsigned char user_{role}_data_{index}[] = {{')
            payload = segment.data or b"\x00"
            for offset in range(0, len(payload), 16):
                lines.append('    ' + ','.join(f'0x{byte:02x}' for byte in payload[offset:offset + 16]) + ',')
            lines.append('};')
        lines.append(f'static const struct user_segment user_{role}_segments[] = {{')
        for index, segment in enumerate(image.segments):
            lines.append(f'    {{0x{segment.va:x}, {segment.mem_size}, {len(segment.data)}, '
                         f'{segment.flags}, user_{role}_data_{index}}},')
        lines.append('};')
    lines.append('static const struct user_image user_images[3] = {')
    for role, image in enumerate(images):
        lines.append(f'    {{0x{image.entry:x}, {len(image.segments)}, user_{role}_segments}},')
    lines.extend(['};', '#endif', ''])
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs=3, metavar="ELF")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        content = render([read_image(path.read_bytes()) for path in args.images])
    except (OSError, ValueError) as exc:
        parser.exit(1, f"pack_users: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
