#!/usr/bin/env python3
"""Run one QEMU case and preserve transport evidence.

A PASS verifies process exit, log markers, failure detection and cleanup only.
This report intentionally has semantic_validation=false. A phase-specific
validator must check actual guest observations before accepting a guest gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

try:
    from .qemu_command import DEFAULT_CONFIG, PROJECT_ROOT, build_qemu_command, load_config
except ImportError:
    from qemu_command import DEFAULT_CONFIG, PROJECT_ROOT, build_qemu_command, load_config

CASE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
MAX_LOG_LINE = 4096
FAILURE_PATTERN = re.compile(
    r"\[PANIC\]|\bkernel panic\b|\bpanic\s*:|\[FAIL\]|"
    r"\[TEST\][^\r\n]*\bFAIL(?:ED)?\b|"
    r"\b(?:assertion|invariant)[^\r\n]{0,128}\b(?:fail(?:ed|ure)?|violat(?:ed|ion))\b",
    re.IGNORECASE,
)


def valid_timeout(value: str | float) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("timeout must be a finite positive number")
    return result


def report_base(path: Path | str, project_root: Path) -> Path:
    raw = Path(path)
    if ".." in raw.parts:
        raise ValueError("report-dir cannot contain '..'")
    root = project_root.resolve()
    destination = (raw if raw.is_absolute() else root / raw).resolve()
    if not destination.is_relative_to(root) or destination == root:
        raise ValueError("report-dir must be a subdirectory of the project")
    return destination


def live_group_members(pgid: int) -> list[int]:
    """Ignore zombies: they cannot execute, retain descriptors, or be killed."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                members.append(int(entry.name))
        except (OSError, IndexError, ValueError):
            continue
    return members


def stop_group(pgid: int, grace: float = 0.25) -> list[int]:
    """Stop descendants even when the original group leader already exited."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not live_group_members(pgid):
            return []
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return []
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not live_group_members(pgid):
                return []
            time.sleep(0.01)
    return live_group_members(pgid)


def execute(command: list[str], timeout: float, stdout, stderr) -> dict:
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        start_new_session=True,
    )
    timed_out = interrupted = False
    descendants = []
    survivors = []
    try:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        except KeyboardInterrupt:
            interrupted = True
        descendants = [pid for pid in live_group_members(process.pid) if pid != process.pid]
    finally:
        survivors = stop_group(process.pid)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
    return {
        "exit_code": process.returncode, "timed_out": timed_out,
        "interrupted": interrupted, "unexpected_descendants": descendants,
        "surviving_processes": survivors,
    }


def git_metadata(root: Path) -> dict:
    result = {"commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=3, check=False,
        )
        if commit.returncode == 0:
            result["commit"] = commit.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root, capture_output=True, text=True, timeout=3, check=False,
            )
            if status.returncode == 0:
                result["dirty"] = bool(status.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def qemu_version(executable: str) -> str | None:
    try:
        with tempfile.TemporaryFile() as output:
            result = execute([executable, "--version"], 3, output, subprocess.STDOUT)
            if result["interrupted"]:
                raise KeyboardInterrupt
            if result["exit_code"] != 0 or result["timed_out"]:
                return None
            output.seek(0)
            return output.read(4096).decode("utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None


def inspect_logs(paths: list[Path], expected: list[str]) -> tuple[dict, list[str]]:
    observed = {marker: False for marker in expected}
    failures = []
    for path in paths:
        with path.open("rb") as source:
            while line := source.readline(MAX_LOG_LINE + 1):
                if len(line) > MAX_LOG_LINE:
                    if len(failures) < 20:
                        failures.append(f"Log line exceeds {MAX_LOG_LINE} bytes: {path.name}")
                    # 故障 guest 可能永不換行；有限讀取，避免收尾解析耗盡記憶體。
                    while line and not line.endswith(b"\n"):
                        line = source.readline(MAX_LOG_LINE + 1)
                    continue
                stripped = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if stripped in observed:
                    observed[stripped] = True
                if FAILURE_PATTERN.search(stripped) and len(failures) < 20:
                    failures.append(stripped[:1024])
    return observed, failures


def run_case(
    config: dict, image: Path | str, case: str, timeout: float = 45,
    markers: list[str] | None = None,
    report_dir: Path | str = "build/reports/qemu", *,
    project_root: Path = PROJECT_ROOT, executable: str | None = None,
    boot_args: str = "",
) -> tuple[dict, Path]:
    if not CASE_PATTERN.fullmatch(case):
        raise ValueError("case must start with a letter and contain only letters, digits, '_' or '-'")
    timeout = valid_timeout(timeout)
    expected = [f"[TEST] CASE={case} START", f"[TEST] CASE={case} PASS"] + (markers or [])
    if any(not isinstance(marker, str) or not marker or "\n" in marker or "\r" in marker for marker in expected):
        raise ValueError("expected markers must be nonempty single lines")
    image = Path(image)
    image = (image if image.is_absolute() else project_root / image).resolve()
    command = build_qemu_command(config, image, executable=executable, boot_args=boot_args)
    base = report_base(report_dir, project_root)
    timestamp = dt.datetime.now(dt.timezone.utc)
    case_parent = (base / case).resolve()
    if not case_parent.is_relative_to(project_root.resolve()):
        raise ValueError("case report directory escapes the project through a symlink")
    case_dir = case_parent / f"{timestamp:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
    case_dir.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = case_dir / "stdout.log", case_dir / "stderr.log"
    stdout_path.touch()
    stderr_path.touch()
    report = {
        "schema_version": 1, "evidence_kind": "qemu_transport", "semantic_validation": False,
        "test_id": case, "started_at": timestamp.isoformat(), "timeout_s": timeout,
        "command": command, "source": git_metadata(project_root),
        "tools": {"python": sys.version.split()[0], "qemu": None},
        "image": {"path": str(image), "sha256": None},
        "exit_code": None, "timed_out": False, "interrupted": False,
        "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
        "expected": expected, "observed": {}, "assertions": [], "status": "BLOCKED",
    }
    started = time.monotonic()
    try:
        if sys.platform != "linux":
            raise OSError("Run this harness with WSL/Linux Python; process-group cleanup requires /proc")
        if not image.is_file() or image.stat().st_size == 0:
            raise OSError(f"Kernel image is missing or empty: {image}")
        with image.open("rb") as source:
            report["image"]["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        report["tools"]["qemu"] = qemu_version(command[0])
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            report.update(execute(command, timeout, stdout, stderr))
        observed, failures = inspect_logs([stdout_path, stderr_path], expected)
        report["observed"] = {"markers": observed, "failure_lines": failures}
        report["assertions"] = [
            {"name": "exit_zero", "passed": report["exit_code"] == 0},
            {"name": "within_timeout", "passed": not report["timed_out"]},
            {"name": "not_interrupted", "passed": not report["interrupted"]},
            {"name": "expected_markers", "passed": all(observed.values())},
            {"name": "no_failure_markers", "passed": not failures},
            {"name": "no_orphan_processes", "passed": not report["unexpected_descendants"]},
            {"name": "process_group_clean", "passed": not report["surviving_processes"]},
        ]
        report["status"] = "PASS" if all(item["passed"] for item in report["assertions"]) else "FAIL"
    except (OSError, ValueError) as error:
        report["error"] = str(error)
    except KeyboardInterrupt:
        report["interrupted"] = True
        report["status"] = "FAIL"
        report["error"] = "Harness interrupted"
    finally:
        report["duration_s"] = round(time.monotonic() - started, 6)
        report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        report_path = case_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--timeout", default=45, type=valid_timeout)
    parser.add_argument("--expect-marker", action="append", default=[])
    parser.add_argument("--report-dir", default="build/reports/qemu")
    parser.add_argument("--executable", help="Override the QEMU executable (also used by harness self-tests)")
    parser.add_argument("--boot-args", default="")
    parser.add_argument("--show-output", action="store_true", help="Print captured guest output after the run")
    parser.add_argument("--dry-run", action="store_true", help="Print argv; do not run or produce evidence")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not CASE_PATTERN.fullmatch(args.case):
            raise ValueError("invalid case name")
        report_base(args.report_dir, PROJECT_ROOT)
        if args.dry_run:
            image = args.image if args.image.is_absolute() else PROJECT_ROOT / args.image
            print(json.dumps({"command": build_qemu_command(config, image, executable=args.executable, boot_args=args.boot_args)}, indent=2))
            return 0
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        report, report_path = run_case(
            config, args.image, args.case, args.timeout, args.expect_marker,
            args.report_dir, executable=args.executable, boot_args=args.boot_args,
        )
        if args.show_output:
            for name in ("stdout_path", "stderr_path"):
                print(Path(report[name]).read_text(encoding="utf-8", errors="replace"), end="")
        print(json.dumps({"status": report["status"], "report": str(report_path)}))
        return 0 if report["status"] == "PASS" else 1
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
