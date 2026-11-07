#!/usr/bin/env python3
"""Validate the project's raw AArch64 Image header without running a guest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys


HEADER = struct.Struct("<IIQQQQQQII")
MAGIC = 0x644D5241
TEXT_OFFSET = 0x80000
UINT64_MAX = (1 << 64) - 1
DRAM_BASE = 0x40000000


def inspect_image(path: Path) -> dict:
    result = {
        "schema_version": 1,
        "kind": "image-inspection",
        "image": str(path.resolve()),
        "status": "INVALID",
        "errors": [],
    }
    errors = result["errors"]
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise OSError("Image must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(HEADER.size)
            size = os.fstat(stream.fileno()).st_size
            digest = hashlib.sha256(raw)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        errors.append(str(error))
        return result

    result.update(file_size=size, sha256=digest.hexdigest())
    if len(raw) != HEADER.size:
        errors.append("Image is shorter than the 64-byte header")
        return result

    code0, code1, offset, memory_size, flags, res2, res3, res4, magic, res5 = HEADER.unpack(raw)
    header = {
        "code0": code0,
        "code1": code1,
        "text_offset": offset,
        "image_size": memory_size,
        "flags": flags,
        "magic": magic,
    }
    result["header"] = header
    if magic != MAGIC:
        errors.append("Invalid AArch64 Image magic")
    if offset != TEXT_OFFSET:
        errors.append("Project text_offset must be 0x80000")
    if flags != 0x2:
        errors.append("Project flags must be 0x2 (little-endian, 4 KiB pages)")
    if memory_size == 0 or memory_size < size:
        errors.append("Nonzero image_size must cover the entire raw file")
    if offset + memory_size > UINT64_MAX:
        errors.append("text_offset + image_size overflows 64 bits")
    elif DRAM_BASE + offset + memory_size > UINT64_MAX:
        errors.append("Physical Image memory range overflows 64 bits")
    if code1 != 0 or any((res2, res3, res4, res5)):
        errors.append("Project reserved fields and code1 must be zero")
    if code0 & 0xFC000000 != 0x14000000:
        errors.append("code0 must be an AArch64 unconditional B instruction")
    else:
        immediate = code0 & 0x03FFFFFF
        if immediate & (1 << 25):
            immediate -= 1 << 26
        branch_offset = immediate * 4
        header["branch_target_offset"] = branch_offset
        if branch_offset < HEADER.size or branch_offset + 4 > size:
            errors.append("Entry branch must target a full instruction after the header in the raw file")

    if not errors:
        result["status"] = "VALID"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--json", action="store_true", help="Print a JSON inspection report")
    args = parser.parse_args(argv)
    result = inspect_image(args.image)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Image: {result['status']} - {result['image']}")
        for error in result["errors"]:
            print(f"  {error}")
    return 0 if result["status"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
