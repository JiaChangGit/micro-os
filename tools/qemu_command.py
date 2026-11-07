#!/usr/bin/env python3
"""Build the project's QEMU argv without invoking a shell."""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "qemu.json"


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        config = json.load(source)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("QEMU config must have schema_version=1")
    for key in ("executable", "machine", "cpu"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"QEMU config requires a nonempty {key}")
    if not re.fullmatch(r"virt(?:-[0-9]+\.[0-9]+)?", config["machine"]):
        raise ValueError("Only the AArch64 virt machine is supported")
    if config["cpu"] != "cortex-a53":
        raise ValueError("The current CPU contract is cortex-a53")
    if type(config.get("memory_mib")) is not int or config["memory_mib"] != 512:
        raise ValueError("The current memory layout requires memory_mib=512")
    if type(config.get("smp")) is not int or config["smp"] != 1:
        raise ValueError("The current kernel contract supports exactly one CPU")
    if config.get("rtc_clock") != "vm":
        raise ValueError("rtc_clock must be vm so RTC pause behavior is explicit")
    expected = {"gic-version": 3, "virtualization": "off", "highmem": "off"}
    properties = config.get("machine_properties")
    if not isinstance(properties, dict) or properties != expected:
        raise ValueError(f"machine_properties must be {expected}")


def build_qemu_command(
    config: dict,
    image: Path | str,
    *,
    profile: str = "test",
    gdb_port: int = 1234,
    executable: str | None = None,
    boot_args: str = "",
) -> list[str]:
    validate_config(config)
    if profile not in ("test", "debug"):
        raise ValueError("profile must be test or debug")
    properties = ",".join(f"{key}={value}" for key, value in config["machine_properties"].items())
    command = [
        executable or config["executable"],
        "-machine", f"{config['machine']},{properties}",
        "-cpu", config["cpu"], "-accel", "tcg",
        "-smp", str(config["smp"]), "-m", f"{config['memory_mib']}M",
        "-display", "none", "-monitor", "none", "-serial", "stdio",
        "-nic", "none", "-no-reboot", "-rtc", f"base=utc,clock={config['rtc_clock']}",
        "-kernel", str(image),
    ]
    if not isinstance(boot_args, str) or len(boot_args) > 63 or any(not 32 <= ord(char) <= 126 for char in boot_args):
        raise ValueError("boot_args must be at most 63 printable ASCII characters")
    if boot_args:
        command += ["-append", boot_args]
    command += ["-semihosting-config", "enable=on,target=native,userspace=off"]
    if profile == "debug":
        if type(gdb_port) is not int or not 1 <= gdb_port <= 65535:
            raise ValueError("gdb_port must be between 1 and 65535")
        command += ["-S", "-gdb", f"tcp:127.0.0.1:{gdb_port}"]
    return command
