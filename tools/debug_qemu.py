#!/usr/bin/env python3
"""啟動等待 GDB 的 QEMU；不執行自動測試或建立 Gate 收據。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from .qemu_command import DEFAULT_CONFIG, build_qemu_command, load_config
except ImportError:
    from qemu_command import DEFAULT_CONFIG, build_qemu_command, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path("build/kernel.img"))
    parser.add_argument("--case", default="p7-demo")
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}; run make all first")
    try:
        command = build_qemu_command(load_config(args.config), args.image.resolve(),
            profile="debug", gdb_port=args.port, boot_args=f"case={args.case}")
        os.execvp(command[0], command)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
