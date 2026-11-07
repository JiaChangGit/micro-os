#!/usr/bin/env python3
"""Print or install the WSL/Ubuntu toolchain; credentials remain on stdin."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

PACKAGES = (
    "build-essential", "make", "git", "python3", "gcc-aarch64-linux-gnu",
    "binutils-aarch64-linux-gnu", "qemu-system-arm", "gdb-multiarch",
    "device-tree-compiler", "clang", "lld",
)


def installation_commands(*, root: bool = False) -> list[list[str]]:
    prefix = [] if root else ["sudo"]
    return [
        prefix + ["apt-get", "update"],
        prefix + ["apt-get", "install", "-y", "--no-install-recommends", *PACKAGES],
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="install using interactive sudo")
    args = parser.parse_args(argv)
    if not args.install:
        for command in installation_commands():
            print(shlex.join(command))
        return 0
    if sys.platform != "linux" or not os.path.isfile("/etc/debian_version"):
        parser.error("run installation inside the Ubuntu WSL distribution")
    # 不接收、儲存或輸出密碼，由 sudo 的互動輸入處理。
    for command in installation_commands(root=os.geteuid() == 0):
        try:
            subprocess.run(command, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"BLOCKED: dependency installation failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
