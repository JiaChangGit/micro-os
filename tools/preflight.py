#!/usr/bin/env python3
"""Inspect build tools and QEMU capabilities; this does not test kernel boot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HOST_TOOLS = ("python3", "git", "gcc", "clang", "make")
TARGET_TOOLS = (
    "qemu-system-aarch64", "aarch64-linux-gnu-gcc", "aarch64-linux-gnu-ld",
    "aarch64-linux-gnu-objcopy", "aarch64-linux-gnu-objdump",
    "aarch64-linux-gnu-readelf", "gdb-multiarch", "dtc",
)
OUTPUT_LIMIT = 65536


def run_probe(command: list[str], timeout: float = 10.0) -> dict:
    """工具輸出先落暫存檔，避免不受限的 pipe 緩衝與記憶體成長。"""
    result = {"command": command, "status": "ERROR"}
    with tempfile.TemporaryFile() as capture:
        try:
            process = subprocess.Popen(
                command, stdout=capture, stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
        except OSError as error:
            result["error"] = str(error)
            return result
        try:
            code = process.wait(timeout=timeout)
            result.update(exit_code=code, status="OK" if code == 0 else "ERROR")
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            result.update(exit_code=process.returncode, status="TIMEOUT")
        capture.seek(0, os.SEEK_END)
        result["output_truncated"] = capture.tell() > OUTPUT_LIMIT
        capture.seek(0)
        result["output"] = capture.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")
    return result


def resource_info(root: Path) -> dict:
    data = {
        "system": platform.system(), "release": platform.release(),
        "machine": platform.machine(), "logical_cpus": os.cpu_count(),
        "memory_total_bytes": None, "memory_available_bytes": None,
    }
    try:
        memory = Path("/proc/meminfo").read_text(encoding="ascii")
        for field, key in (("MemTotal", "memory_total_bytes"), ("MemAvailable", "memory_available_bytes")):
            match = re.search(rf"^{field}:\s+(\d+)\s+kB$", memory, re.MULTILINE)
            if match:
                data[key] = int(match.group(1)) * 1024
    except OSError:
        pass
    try:
        with Path("/proc/cpuinfo").open(encoding="utf-8") as stream:
            cpu = stream.read(131072)
        match = re.search(r"^(?:model name|Hardware)\s*:\s*(.+)$", cpu, re.MULTILINE)
        if match:
            data["cpu_model"] = match.group(1)
    except OSError:
        pass
    disk = shutil.disk_usage(root)
    data.update(disk_total_bytes=disk.total, disk_free_bytes=disk.free)
    return data


def inspect_environment(profile: str = "target") -> dict:
    if profile not in ("host", "target"):
        raise ValueError("Unknown preflight profile")
    report = {
        "schema_version": 1, "kind": "environment-preflight",
        "created_utc": datetime.now(timezone.utc).isoformat(), "profile": profile,
        "status": "BLOCKED", "environment": resource_info(ROOT),
        "tools": {}, "capabilities": {}, "blockers": [],
    }
    if platform.system() != "Linux":
        report["blockers"].append("Run project tools in Linux/WSL, not a Windows Python interpreter")

    required = HOST_TOOLS + (TARGET_TOOLS if profile == "target" else ())
    for name in required:
        executable = shutil.which(name)
        if executable is None:
            report["tools"][name] = {"status": "MISSING"}
            report["blockers"].append(f"Missing required tool: {name}")
            continue
        check = run_probe([executable, "--version"])
        check["path"] = executable
        report["tools"][name] = check
        if check["status"] != "OK":
            report["blockers"].append(f"Version probe failed: {name}")

    qemu = report["tools"].get("qemu-system-aarch64", {})
    if profile == "target" and qemu.get("status") == "OK":
        probes = {
            "virt_machine": (["-machine", "help"], r"(?m)^\s*virt\s"),
            "cortex_a53": (["-cpu", "help"], r"\bcortex-a53\b"),
            "virt_properties": (["-machine", "virt,help"], r"gic-version"),
        }
        for name, (arguments, expression) in probes.items():
            check = run_probe([qemu["path"], *arguments])
            supported = check["status"] == "OK" and not check["output_truncated"] and bool(re.search(expression, check.get("output", "")))
            if name == "virt_properties":
                supported = supported and all(word in check.get("output", "") for word in ("virtualization", "highmem"))
            check["supported"] = supported
            report["capabilities"][name] = check
            if not supported:
                report["blockers"].append(f"QEMU capability unavailable or unconfirmed: {name}")

    compiler = report["tools"].get("aarch64-linux-gnu-gcc", {})
    if profile == "target" and compiler.get("status") == "OK":
        check = run_probe([compiler["path"], "-dumpmachine"])
        check["supported"] = check["status"] == "OK" and check.get("output", "").strip().startswith("aarch64")
        report["capabilities"]["compiler_target"] = check
        if not check["supported"]:
            report["blockers"].append("Cross compiler does not confirm an AArch64 target")

    if not report["blockers"]:
        report["status"] = "READY"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("host", "target"), default="target")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report")
    parser.add_argument("--output", type=Path, help="Optionally save the report; default inspection writes no project files")
    args = parser.parse_args(argv)
    report = inspect_environment(args.profile)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        except OSError as error:
            print(f"Cannot write preflight report: {error}", file=sys.stderr)
            return 2
    if args.json:
        print(serialized, end="")
    else:
        print(f"Environment: {report['status']} ({args.profile})")
        for name, info in report["tools"].items():
            first_line = info.get("output", "").splitlines()
            version = first_line[0] if first_line else info.get("path", "")
            print(f"  {name}: {info['status']} {version}".rstrip())
        for blocker in report["blockers"]:
            print(f"  BLOCKER: {blocker}")
        print("This inspection does not validate guest boot or any phase gate.")
    return 0 if report["status"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
