#!/usr/bin/env python3
"""Validate implemented phase behavior and write evidence-backed gate receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.inspect_image import inspect_image
from tools.project import ROOT, file_sha256, load_manifest, resolve_project_path, source_fingerprint
from tools.qemu_command import load_config
from tools.run_qemu import execute, git_metadata, run_case


def project_file(path: str | Path, root: Path = ROOT) -> Path:
    value = Path(path)
    if value.is_absolute():
        value = value.resolve().relative_to(root.resolve())
    return resolve_project_path(root, value.as_posix())


def assertion(name: str, passed: bool, *, expected=None, observed=None) -> dict:
    result = {"name": name, "passed": bool(passed)}
    if expected is not None:
        result["expected"] = expected
    if observed is not None:
        result["observed"] = observed
    return result


def write_receipt(
    gate: str, phase: str, command: list[str], assertions: list[dict], evidence: list[Path | str],
    *, root: Path = ROOT, fingerprint: str | None = None, exit_code: int = 0,
    metadata: dict | None = None,
) -> tuple[dict, Path]:
    """Record observed results; no PASS argument or editable completion flag."""
    manifest = load_manifest(root)
    definition = next((item for item in manifest["phases"] if item["id"] == phase), None)
    if definition is None or gate not in definition["gates"]:
        raise ValueError("Gate does not belong to the requested phase")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item.strip() or "\0" in item for item in command):
        raise ValueError("Actual validator command is required")
    if type(exit_code) is not int or not isinstance(assertions, list) or not assertions or not isinstance(evidence, list) or not evidence:
        raise ValueError("Exit code, semantic assertions and raw evidence are required")
    names = set()
    for item in assertions:
        if not isinstance(item, dict):
            raise ValueError("Semantic assertions must be objects")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in names or type(item.get("passed")) is not bool:
            raise ValueError("Semantic assertions need unique names and boolean results")
        names.add(name)
    evidence_records = []
    for value in evidence:
        path = project_file(value, root)
        if not path.is_file():
            raise ValueError(f"Raw evidence is missing: {value}")
        evidence_records.append({"path": path.relative_to(root.resolve()).as_posix(), "sha256": file_sha256(path)})
    record = {
        "schema_version": 1, "gate_id": gate, "phase": phase,
        "status": "PASS" if exit_code == 0 and all(item["passed"] for item in assertions) else "FAIL",
        "evidence_kind": "phase_gate", "semantic_validation": True,
        "source_fingerprint": fingerprint or source_fingerprint(root),
        "command": command, "exit_code": exit_code, "assertions": assertions,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence_records, "source": git_metadata(root),
    }
    if metadata:
        record["metadata"] = metadata
    destination = resolve_project_path(root, f"build/reports/gates/{gate}.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return record, destination


def dts_platform(text: str) -> dict[str, int]:
    """Read only dtc's controlled QEMU root-device output, not arbitrary DTS."""
    nodes = []
    stack = []
    for line in text.splitlines():
        line = line.strip()
        if line.endswith("{"):
            stack.append({"name": line[:-1].strip(), "depth": len(stack), "properties": {}})
        elif line == "};":
            if not stack:
                raise ValueError("Unbalanced dtc output")
            nodes.append(stack.pop())
        elif stack and (match := re.fullmatch(r"([#A-Za-z0-9_,.-]+)\s*=\s*(.+);", line)):
            stack[-1]["properties"][match[1]] = match[2]
    if stack:
        raise ValueError("Unbalanced dtc output")

    def node(*, compatible=None, device_type=None):
        found = [item for item in nodes if item["depth"] == 1 and (
            (compatible is not None and f'"{compatible}"' in item["properties"].get("compatible", "")) or
            (device_type is not None and item["properties"].get("device_type") == f'"{device_type}"')
        )]
        if len(found) != 1:
            raise ValueError(f"Expected one root device: {compatible or device_type}")
        return found[0]["properties"]

    def cells(properties, key, count):
        value = properties.get(key, "")
        if not re.fullmatch(r"<[0-9a-fA-FxX\s]+>", value):
            raise ValueError(f"Unsupported dtc {key} cells")
        result = [int(item, 0) for item in value[1:-1].split()]
        if len(result) != count or any(not 0 <= item <= 0xFFFFFFFF for item in result):
            raise ValueError(f"Unexpected dtc {key} cell count/value")
        return result

    def pair(values, at=0):
        return (values[at] << 32) | values[at + 1]

    memory = cells(node(device_type="memory"), "reg", 4)
    uart = cells(node(compatible="arm,pl011"), "reg", 4)
    rtc_node = node(compatible="arm,pl031")
    rtc = cells(rtc_node, "reg", 4)
    rtc_irq = cells(rtc_node, "interrupts", 3)
    gic = cells(node(compatible="arm,gic-v3"), "reg", 8)
    timer = cells(node(compatible="arm,armv8-timer"), "interrupts", 12)
    if rtc_irq[0] != 0 or timer[3] != 1:
        raise ValueError("Expected RTC SPI and nonsecure physical timer PPI")
    return {
        "ram_base": pair(memory), "ram_size": pair(memory, 2),
        "uart": pair(uart), "rtc": pair(rtc), "gicd": pair(gic), "gicr": pair(gic, 4),
        "rtc_irq": rtc_irq[1] + 32, "timer_irq": timer[4] + 16,
    }


def guest_records(log: str, event: str, fields: set[str]) -> list[dict[str, int]]:
    lines = [line for line in log.splitlines() if line.startswith(f"[{event}] ")]
    records = []
    for line in lines:
        result = {}
        for token in line.split()[1:]:
            match = re.fullmatch(r"([a-z_]+)=(0x[0-9a-fA-F]+|[0-9]+)", token)
            if not match or match[1] in result:
                raise ValueError(f"Malformed {event} field")
            result[match[1]] = int(match[2], 0)
        if not fields.issubset(result):
            raise ValueError(f"Missing {event} fields")
        records.append(result)
    return records


def guest_record(log: str, event: str, fields: set[str]) -> dict[str, int]:
    records = guest_records(log, event, fields)
    if len(records) != 1:
        raise ValueError(f"Expected exactly one {event} record")
    return records[0]


def run_p0(image: Path, dtb: Path, command: list[str], *, root: Path = ROOT) -> bool:
    config = load_config(root / "config/qemu.json")
    fingerprint = source_fingerprint(root)
    folder = resolve_project_path(root, "build/reports/validation/p0")
    folder.mkdir(parents=True, exist_ok=True)
    image_result = inspect_image(image)
    image_checks = [assertion("image_header_valid", image_result["status"] == "VALID")]
    memory_size = image_result.get("header", {}).get("image_size", 0)
    image_checks.append(assertion("image_fits_guest_ram", 0 < memory_size <= config["memory_mib"] * 1024 * 1024 - 0x80000))
    if image_result["status"] == "VALID":
        raw = image.read_bytes()
        variants = {
            "truncated": raw[:32],
            "bad_magic": raw[:56] + b"BAD!" + raw[60:],
            "bad_size": raw[:16] + struct.pack("<Q", 1) + raw[24:],
            "bad_branch": struct.pack("<I", 0x14000000) + raw[4:],
        }
        for name, contents in variants.items():
            path = folder / f"{name}.img"
            path.write_bytes(contents)
            outcome = inspect_image(path)
            image_checks.append(assertion(f"reject_{name}", outcome["status"] == "INVALID"))
            image_result[f"negative_{name}"] = outcome
    image_diagnostic = folder / "image.json"
    image_diagnostic.write_text(json.dumps(image_result, indent=2) + "\n", encoding="utf-8")
    image_checks.append(assertion("source_unchanged", source_fingerprint(root) == fingerprint))
    image_evidence = [image_diagnostic] + ([image] if image.is_file() else [])
    image_receipt, image_receipt_path = write_receipt(
        "p0-image", "P0", command, image_checks, image_evidence, root=root, fingerprint=fingerprint,
        exit_code=0 if all(item["passed"] for item in image_checks) else 1,
    )
    print(f"p0-image: {image_receipt['status']} ({image_receipt_path.relative_to(root)})")

    transport, transport_path = run_case(config, image, "p0-boot", timeout=15, project_root=root, boot_args="case=p0-boot")
    checks = [assertion("transport_pass", transport["status"] == "PASS")]
    evidence = [transport_path, Path(transport["stdout_path"]), Path(transport["stderr_path"])]
    diagnostic = {"transport": transport["status"]}
    try:
        if not dtb.is_file() or dtb.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Platform DTB is missing or too large")
        header = dtb.read_bytes()[:8]
        if len(header) != 8 or struct.unpack(">I", header[:4])[0] != 0xD00DFEED:
            raise ValueError("Invalid platform DTB header")
        result = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(dtb)], capture_output=True, timeout=5, check=False)
        dts = folder / "platform.dts"
        dts.write_bytes(result.stdout)
        evidence.extend([dtb, dts])
        if result.returncode:
            raise ValueError("dtc failed to decode the platform DTB")
        expected = dts_platform(result.stdout.decode("utf-8"))
        diagnostic["expected"] = expected
        stdout = Path(transport["stdout_path"])
        if stdout.stat().st_size > 1024 * 1024:
            raise ValueError("Boot output exceeds the semantic parser limit")
        log = stdout.read_text(encoding="utf-8")
        boot = guest_record(log, "BOOT", {"el", "fdt", "size"})
        platform = guest_record(log, "PLATFORM", set(expected))
        diagnostic.update(boot=boot, platform=platform)
        checks.append(assertion("current_el_is_one", boot["el"] == 1, expected=1, observed=boot["el"]))
        checks.append(assertion("fdt_size_valid", 40 <= boot["size"] <= 2 * 1024 * 1024))
        checks.append(assertion("fdt_is_aligned_in_ram", boot["fdt"] % 8 == 0 and expected["ram_base"] <= boot["fdt"] <= expected["ram_base"] + expected["ram_size"] - boot["size"]))
        checks.append(assertion("ram_matches_profile", expected["ram_base"] == 0x40000000 and expected["ram_size"] == config["memory_mib"] * 1024 * 1024))
        for key, value in expected.items():
            checks.append(assertion(f"dtb_matches_{key}", platform[key] == value, expected=value, observed=platform[key]))
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError) as error:
        diagnostic["error"] = str(error)
        checks.append(assertion("boot_observations_valid", False, observed=str(error)))
    checks.append(assertion("source_unchanged", source_fingerprint(root) == fingerprint))
    diagnostic_path = folder / "boot.json"
    diagnostic_path.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")
    evidence.append(diagnostic_path)
    boot_receipt, boot_receipt_path = write_receipt(
        "p0-boot", "P0", command, checks, evidence, root=root, fingerprint=fingerprint,
        exit_code=0 if all(item["passed"] for item in checks) else 1,
        metadata={"tools": transport["tools"], "qemu_command": transport["command"]},
    )
    print(f"p0-boot: {boot_receipt['status']} ({boot_receipt_path.relative_to(root)})")
    return image_receipt["status"] == boot_receipt["status"] == "PASS"


def p1_vm_assertions(log: str, memory_mib: int = 512) -> list[dict]:
    vm = guest_record(log, "VM", {"enabled", "granule", "root", "high_pc", "text_ro", "data_xn", "reserved"})
    exception = guest_record(log, "EXCEPTION", {"el", "ec", "recovered"})
    return [
        assertion("mmu_enabled", vm["enabled"] == 1),
        assertion("four_kib_granule", vm["granule"] == 4096),
        assertion("root_table_aligned_in_ram", vm["root"] % 4096 == 0 and 0x40000000 <= vm["root"] < 0x40000000 + memory_mib * 1024 * 1024),
        assertion("pc_in_ttbr1_region", 0xFFFFFF8000000000 <= vm["high_pc"] <= 0xFFFFFFFFFFFFFFFF),
        assertion("kernel_text_read_only", vm["text_ro"] == 1),
        assertion("kernel_data_execute_never", vm["data_xn"] == 1),
        assertion("reserved_memory_excluded", vm["reserved"] == 1),
        assertion("el1_brk_observed", exception["el"] == 1 and exception["ec"] == 60),
        assertion("el1_brk_returned", exception["recovered"] == 1),
    ]


P2_FAULTS = {
    "p2-mmio": (36, 0x20000000), "p2-text": (36, 0x00400000),
    "p2-stack": (36, 0x007FB000), "p2-kernel": (36, 0x40080000),
    "p2-xn": (32, 0x007FF000), "p2-fp": (7, 0),
}


def p2_assertions(log: str, case: str) -> list[dict]:
    tasks = guest_records(log, "TASK", {"role", "el", "instance"})
    isolation = guest_record(log, "ISOLATION", {"role", "ec", "far", "svc", "invalid_syscall", "bad_pointer", "continued"})
    ec, far = P2_FAULTS[case]
    return [
        assertion("three_distinct_el0_roles", len(tasks) == 3 and {task["role"] for task in tasks} == {0, 1, 2}
                  and all(task["el"] == 0 and task["instance"] == 1 for task in tasks)),
        assertion("only_client_faulted", isolation["role"] == 2),
        assertion("expected_exception_class", isolation["ec"] == ec, expected=ec, observed=isolation["ec"]),
        assertion("far_not_applicable_to_fp_trap" if ec == 7 else "expected_fault_address",
                  isolation["far"] == far, expected=far, observed=isolation["far"]),
        assertion("svc_executed", isolation["svc"] >= 3, observed=isolation["svc"]),
        assertion("invalid_syscall_rejected", isolation["invalid_syscall"] >= 1),
        assertion("invalid_user_pointer_rejected", isolation["bad_pointer"] >= 1),
        assertion("survivor_resumed_after_fault", isolation["continued"] >= 1),
    ]


def p3_assertions(log: str, *, idle_case: bool = False) -> list[dict]:
    sched = guest_record(log, "SCHED", {"timer_irq", "preemptions", "sleep_wake", "idle", "invariants"})
    checks = [
        assertion("physical_timer_interrupt_received", sched["timer_irq"] > 0, observed=sched["timer_irq"]),
        assertion("blocked_thread_woke", sched["sleep_wake"] > 0, observed=sched["sleep_wake"]),
        assertion("runtime_state_invariants", sched["invariants"] == 1),
    ]
    checks.append(assertion("idle_entered" if idle_case else "timer_preempted_user",
                            sched["idle" if idle_case else "preemptions"] > 0,
                            observed=sched["idle" if idle_case else "preemptions"]))
    return checks


def ipc_accounting_assertions(log: str, peer_deaths: int = 0) -> list[dict]:
    ipc = guest_record(log, "IPC", {"calls", "replies", "busy", "timeout", "stale", "denied", "completed", "once"})
    return [
        assertion("rpc_replied", ipc["replies"] > 0),
        assertion("accepted_calls_accounted", ipc["calls"] == ipc["completed"], expected=ipc["calls"], observed=ipc["completed"]),
        assertion("completions_accounted", ipc["completed"] == ipc["replies"] + ipc["timeout"] + peer_deaths,
                  expected=ipc["replies"] + ipc["timeout"] + peer_deaths, observed=ipc["completed"]),
        assertion("single_completion_invariant", ipc["once"] == 1),
    ]


def p4_assertions(log: str) -> list[dict]:
    ipc = guest_record(log, "IPC", {"busy", "timeout", "stale", "denied"})
    return [
        *ipc_accounting_assertions(log),
        assertion("busy_endpoint_rejected", ipc["busy"] > 0),
        assertion("finite_call_timeout", ipc["timeout"] > 0),
        assertion("old_reply_token_rejected", ipc["stale"] > 0),
        assertion("rights_failure_observed", ipc["denied"] > 0),
    ]


def p5_assertions(log: str, case: str) -> list[dict]:
    rtc = guest_record(log, "RTC", {"alarm", "irq", "timeout", "early", "ack", "ack_rejected"})
    checks = [assertion("alarm_requests_accepted", rtc["alarm"] > 0)]
    if case == "p5-timeout":
        checks.append(assertion("missing_irq_bounded_wait", rtc["timeout"] > 0))
    else:
        checks.extend([
            assertion("rtc_irq_received", rtc["irq"] > 0),
            assertion("rtc_irq_acknowledged", rtc["ack"] > 0),
            assertion("ack_not_greater_than_irq", rtc["ack"] <= rtc["irq"]),
            assertion("ack_before_device_clear_rejected", rtc["ack_rejected"] > 0),
        ])
    if case == "p5-early":
        checks.append(assertion("irq_before_wait_observed", rtc["early"] > 0))
    if case == "p5-client-timeout":
        ipc = guest_record(log, "IPC", {"timeout", "stale", "busy"})
        checks.extend([
            *ipc_accounting_assertions(log),
            assertion("client_timeout_observed", ipc["timeout"] > 0),
            assertion("late_service_reply_rejected", ipc["stale"] > 0),
            assertion("service_remained_busy_after_client_timeout", ipc["busy"] > 0),
        ])
    return checks


def restart_assertions(log: str, minimum: int = 3) -> list[dict]:
    restart = guest_record(log, "RESTART", {"count", "peer_died", "old_token", "masked", "resource_delta"})
    deaths = guest_records(log, "DEATH", {"role", "instance", "epoch", "esr", "far", "masked"})
    rebuilds = guest_records(log, "REBUILD", {"instance", "epoch", "tables"})
    rtc_tasks = [task for task in guest_records(log, "TASK", {"role", "el", "instance"}) if task["role"] == 1]
    count = restart["count"]
    events = [line.split()[0] for line in log.splitlines() if line.startswith(("[DEATH] ", "[REBUILD] "))]
    return [
        assertion("restart_iterations_completed", count >= minimum, expected=minimum, observed=count),
        assertion("death_and_rebuild_count_matches", len(deaths) == len(rebuilds) == count, expected=count,
                  observed={"death": len(deaths), "rebuild": len(rebuilds)}),
        assertion("death_precedes_each_rebuild", len(events) == 2 * count
                  and all(event == ("[DEATH]" if index % 2 == 0 else "[REBUILD]") for index, event in enumerate(events))),
        assertion("instance_and_epoch_advance", all(death["role"] == 1 and death["instance"] == index + 1
                  and death["epoch"] == index + 2 for index, death in enumerate(deaths))
                  and all(rebuild["instance"] == rebuild["epoch"] == index + 2 for index, rebuild in enumerate(rebuilds))),
        assertion("each_rebuilt_instance_entered_el0", len(rtc_tasks) == count + 1
                  and all(task["el"] == 0 and task["instance"] == index + 1 for index, task in enumerate(rtc_tasks))),
        assertion("waiting_client_observed_each_peer_death", restart["peer_died"] == count),
        assertion("stale_reply_rejected_after_each_restart", restart["old_token"] == count),
        assertion("irq_masked_for_each_restart", restart["masked"] == count
                  and all(death["masked"] == 1 for death in deaths)),
        assertion("rebuild_table_count_constant", bool(rebuilds) and rebuilds[0]["tables"] > 0
                  and len({rebuild["tables"] for rebuild in rebuilds}) == 1,
                  observed=[rebuild["tables"] for rebuild in rebuilds]),
        assertion("fixed_resource_baseline_restored", restart["resource_delta"] == 0),
    ]


def recovery_assertions(log: str, minimum: int = 3) -> list[dict]:
    restart = guest_record(log, "RESTART", {"peer_died"})
    return [*restart_assertions(log, minimum), *ipc_accounting_assertions(log, restart["peer_died"])]


def demo_assertions(log: str) -> list[dict]:
    return [*recovery_assertions(log), *p5_assertions(log, "p5-rtc")]


def pending_restart_assertions(log: str) -> list[dict]:
    rtc = guest_record(log, "RTC", {"alarm", "irq", "ack", "ack_rejected"})
    deaths = guest_records(log, "DEATH", {"device_pending"})
    return [
        *recovery_assertions(log, 1),
        assertion("alarm_requests_accepted_at_least_twice", rtc["alarm"] >= 2),
        assertion("irq_before_fault_and_after_restart", rtc["irq"] >= 2),
        assertion("new_instance_acknowledged_irq", rtc["ack"] >= 1),
        assertion("fault_observed_with_device_pending", bool(deaths) and deaths[0]["device_pending"] == 1),
        assertion("pending_irq_ack_rejected", rtc["ack_rejected"] > 0),
    ]


def pending_death_event_assertions(log: str) -> list[dict]:
    event = guest_record(log, "RESTART_WAIT", {"instance", "epoch"})
    ordered = log.index("[DEATH] ") < log.index("[RESTART_WAIT] ") < log.index("[REBUILD] ")
    return [*recovery_assertions(log), assertion("restart_waited_for_death_event",
            event["instance"] == 1 and event["epoch"] == 2 and ordered)]


def run_guest_gate(
    gate: str, phase: str, case: str, image: Path, command: list[str], checker,
    *, root: Path = ROOT, fingerprint: str | None = None,
) -> bool:
    return run_guest_cases(gate, phase, {case: checker}, image, command, root=root, fingerprint=fingerprint)


def run_guest_cases(
    gate: str, phase: str, cases: dict, image: Path, command: list[str],
    *, root: Path = ROOT, fingerprint: str | None = None, timeout: float = 15,
) -> bool:
    if not cases:
        raise ValueError("At least one actual guest case is required")
    config = load_config(root / "config/qemu.json")
    fingerprint = fingerprint or source_fingerprint(root)
    checks, evidence, runs = [], [image], []
    for case, checker in cases.items():
        transport, transport_path = run_case(config, image, case, timeout=timeout, project_root=root, boot_args=f"case={case}")
        case_checks = [assertion("transport_pass", transport["status"] == "PASS")]
        diagnostic = {}
        try:
            stdout = Path(transport["stdout_path"])
            if stdout.stat().st_size > 1024 * 1024:
                raise ValueError("Guest output exceeds the semantic parser limit")
            observed = checker(stdout.read_text(encoding="utf-8"))
            if not isinstance(observed, list) or not observed:
                raise ValueError("Guest checker produced no semantic observations")
            case_checks.extend(observed)
        except (OSError, ValueError, UnicodeError) as error:
            diagnostic["error"] = str(error)
            case_checks.append(assertion("guest_observations_valid", False, observed=str(error)))
        diagnostic_path = transport_path.parent / "semantic.json"
        diagnostic_path.write_text(json.dumps({"assertions": case_checks, **diagnostic}, indent=2) + "\n", encoding="utf-8")
        evidence.extend([transport_path, Path(transport["stdout_path"]), Path(transport["stderr_path"]), diagnostic_path])
        checks.extend({**item, "name": f"{case}:{item['name']}"} for item in case_checks)
        runs.append({"case": case, "tools": transport["tools"], "qemu_command": transport["command"]})
    checks.append(assertion("source_unchanged", source_fingerprint(root) == fingerprint))
    record, path = write_receipt(
        gate, phase, command, checks, evidence,
        root=root, fingerprint=fingerprint, exit_code=0 if all(item["passed"] for item in checks) else 1,
        metadata={"runs": runs},
    )
    print(f"{gate}: {record['status']} ({path.relative_to(root)})")
    return record["status"] == "PASS"


def host_assertions(log: str, label: str) -> list[dict]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", label):
        raise ValueError("Invalid host test label")
    matches = re.findall(rf"^\[HOST\] {re.escape(label)}=PASS assertions=([0-9]+)$", log, re.MULTILINE)
    return [
        assertion(f"{label}_checks_executed", len(matches) == 1 and int(matches[0]) > 0,
                  observed=int(matches[0]) if len(matches) == 1 else len(matches)),
        assertion("sanitizers_clean", not re.search(r"AddressSanitizer|UndefinedBehaviorSanitizer|runtime error:", log)),
    ]


def run_host_gate(
    gate: str, phase: str, label: str, sources: list[str], command: list[str],
    *, root: Path = ROOT, fingerprint: str | None = None,
) -> bool:
    fingerprint = fingerprint or source_fingerprint(root)
    folder = resolve_project_path(root, f"build/reports/validation/{gate}")
    folder.mkdir(parents=True, exist_ok=True)
    binary = resolve_project_path(root, f"build/host/test-{label}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    compile_command = [
        "gcc", "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", f"-I{root / 'include'}",
        *(str(project_file(source, root)) for source in sources), "-o", str(binary),
    ]
    compile_log, run_log = folder / "compiler.log", folder / "host.log"
    checks = []
    metadata = {"compile_command": compile_command}
    with compile_log.open("wb") as output:
        compiled = execute(compile_command, 45, output, subprocess.STDOUT)
    checks.append(assertion("sanitizer_build_succeeded", compiled["exit_code"] == 0 and not compiled["timed_out"]))
    run_log.write_bytes(b"")
    if checks[0]["passed"]:
        with run_log.open("wb") as output:
            executed = execute([str(binary)], 10, output, subprocess.STDOUT)
        checks.append(assertion("host_test_exited_zero", executed["exit_code"] == 0 and not executed["timed_out"]))
        if run_log.stat().st_size > 1024 * 1024:
            checks.append(assertion("host_output_bounded", False))
        else:
            log = run_log.read_text(encoding="utf-8", errors="replace")
            checks.extend(host_assertions(log, label))
    checks.append(assertion("source_unchanged", source_fingerprint(root) == fingerprint))
    record, path = write_receipt(
        gate, phase, command, checks, [compile_log, run_log], root=root, fingerprint=fingerprint,
        exit_code=0 if all(item["passed"] for item in checks) else 1, metadata=metadata,
    )
    print(f"{gate}: {record['status']} ({path.relative_to(root)})")
    return record["status"] == "PASS"


def run_p1(image: Path, command: list[str], *, root: Path = ROOT) -> bool:
    fingerprint = source_fingerprint(root)
    host_pass = run_host_gate("p1-pte", "P1", "pte", ["tests/host/test_pte.c"], command, root=root, fingerprint=fingerprint)
    memory_mib = load_config(root / "config/qemu.json")["memory_mib"]
    vm_pass = run_guest_gate("p1-vm", "P1", "p1-vm", image, command, lambda log: p1_vm_assertions(log, memory_mib), root=root, fingerprint=fingerprint)
    return host_pass and vm_pass


def run_later_phase(phase: str, image: Path, command: list[str], *, root: Path = ROOT) -> bool:
    fingerprint = source_fingerprint(root)
    if phase == "P2":
        cases = {case: lambda log, case=case: p2_assertions(log, case) for case in P2_FAULTS}
        return run_guest_cases("p2-isolation", phase, cases, image, command, root=root, fingerprint=fingerprint)
    if phase == "P3":
        host_pass = run_host_gate("p3-state", phase, "state", ["kernel/state.c", "tests/host/test_state.c"], command, root=root, fingerprint=fingerprint)
        guest_pass = run_guest_cases("p3-sched", phase, {"p3-sched": p3_assertions, "p3-idle": lambda log: p3_assertions(log, idle_case=True)}, image, command, root=root, fingerprint=fingerprint)
        return host_pass and guest_pass
    if phase == "P4":
        host_pass = run_host_gate("p4-ipc-model", phase, "exchange", ["kernel/state.c", "tests/host/test_exchange.c"], command, root=root, fingerprint=fingerprint)
        guest_pass = run_guest_gate("p4-ipc", phase, "p4-ipc", image, command, p4_assertions, root=root, fingerprint=fingerprint)
        return host_pass and guest_pass
    if phase == "P5":
        cases = {case: lambda log, case=case: p5_assertions(log, case)
                 for case in ("p5-rtc", "p5-timeout", "p5-early", "p5-client-timeout")}
        return run_guest_cases("p5-rtc", phase, cases, image, command, root=root, fingerprint=fingerprint)
    if phase == "P6":
        return run_guest_cases("p6-restart", phase, {
            "p6-restart": recovery_assertions, "p6-pending": pending_restart_assertions,
            "p6-exit": recovery_assertions, "p6-event": pending_death_event_assertions,
        }, image, command, root=root, fingerprint=fingerprint)
    if phase == "P7":
        regression = run_guest_cases("p7-regression", phase, {"p7-regression": lambda log: recovery_assertions(log, 100)}, image, command, root=root, fingerprint=fingerprint, timeout=45)
        demo = run_guest_gate("p7-demo", phase, "p7-demo", image, command,
                              demo_assertions,
                              root=root, fingerprint=fingerprint)
        return regression and demo
    raise ValueError(f"No validator for phase {phase}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=[f"P{number}" for number in range(8)])
    parser.add_argument("--image", default="build/kernel.img")
    parser.add_argument("--dtb", default="build/platform.dtb")
    args = parser.parse_args(argv)
    command = [sys.executable, "tools/gates.py", *(argv if argv is not None else sys.argv[1:])]
    try:
        image = project_file(args.image)
        dtb = project_file(args.dtb)
        if args.phase == "P0":
            passed = run_p0(image, dtb, command)
        elif args.phase == "P1":
            passed = run_p1(image, command)
        else:
            passed = run_later_phase(args.phase, image, command)
        return 0 if passed else 1
    except (OSError, ValueError, TypeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
