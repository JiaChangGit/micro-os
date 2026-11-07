"""Receipt and log parsing tests use temporary fixtures, not guest evidence."""
from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock

from tools.gates import (
    P2_FAULTS, demo_assertions, dts_platform, guest_record, host_assertions, p1_vm_assertions,
    p2_assertions, p3_assertions, p4_assertions, p5_assertions, pending_death_event_assertions, pending_restart_assertions,
    restart_assertions, run_guest_cases, write_receipt,
)
from tools.phase import phase_statuses
from tools.qemu_command import DEFAULT_CONFIG


DTS = '''/dts-v1/;
/ {
    memory@40000000 {
        reg = <0x0 0x40000000 0x0 0x20000000>;
        device_type = "memory";
    };
    pl011@9000000 {
        compatible = "arm,pl011", "arm,primecell";
        reg = <0x0 0x9000000 0x0 0x1000>;
    };
    pl031@9010000 {
        compatible = "arm,pl031", "arm,primecell";
        reg = <0x0 0x9010000 0x0 0x1000>;
        interrupts = <0x0 0x2 0x4>;
    };
    intc@8000000 {
        compatible = "arm,gic-v3";
        reg = <0x0 0x8000000 0x0 0x10000 0x0 0x80a0000 0x0 0xf60000>;
        its@8080000 {
            compatible = "arm,gic-v3-its";
            reg = <0x0 0x8080000 0x0 0x20000>;
        };
    };
    timer {
        compatible = "arm,armv8-timer", "arm,armv7-timer";
        interrupts = <0x1 0xd 0x4 0x1 0xe 0x4 0x1 0xb 0x4 0x1 0xa 0x4>;
    };
};
'''


class GateWriterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "contract.md").write_text("Temporary unit test contract")
        (self.root / "config/phases.json").write_text(json.dumps({
            "schema_version": 1, "contract": "contract.md", "topics": {},
            "phases": [{"id": "P0", "title": "Fixture", "depends_on": [], "gates": ["p0-image"], "topics": [], "freeze": ["fixture"]}],
        }))
        (self.root / "build").mkdir()
        self.raw = self.root / "build/raw.log"
        self.raw.write_text("unit fixture observation")

    def test_receipt_status_is_derived_from_assertions(self):
        record, path = write_receipt("p0-image", "P0", ["unit-fixture"], [{"name": "observed", "passed": True}], [self.raw], root=self.root)
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(phase_statuses(root=self.root)[0]["status"], "PASS")
        self.assertTrue(path.is_file())
        record, _ = write_receipt("p0-image", "P0", ["unit-fixture"], [{"name": "observed", "passed": False}], [self.raw], root=self.root)
        self.assertEqual(record["status"], "FAIL")
        self.assertEqual(phase_statuses(root=self.root)[0]["status"], "INVALID")

    def test_nonzero_exit_cannot_be_hidden_by_assertions(self):
        record, _ = write_receipt("p0-image", "P0", ["unit-fixture"], [{"name": "observed", "passed": True}], [self.raw], root=self.root, exit_code=1)
        self.assertEqual(record["status"], "FAIL")

    def test_missing_or_invalid_inputs_do_not_write_receipts(self):
        arguments = ("p0-image", "P0", ["unit-fixture"], [{"name": "a", "passed": True}], [self.raw])
        for index, bad in ((0, "../escape"), (1, "P9"), (2, []), (2, "not-argv"), (3, []), (3, ["not-an-assertion"]), (3, [{"name": "a", "passed": 1}]), (4, []), (4, [self.root / "absent"])):
            values = list(arguments)
            values[index] = bad
            with self.subTest(index=index, bad=bad), self.assertRaises(ValueError):
                write_receipt(*values, root=self.root)
        self.assertFalse((self.root / "build/reports/gates").exists())

    def test_evidence_outside_project_is_rejected(self):
        with self.assertRaises(ValueError):
            write_receipt("p0-image", "P0", ["unit-fixture"], [{"name": "a", "passed": True}], [self.root.parent / "outside"], root=self.root)

    def test_case_group_keeps_partial_failure_evidence_and_fails_closed(self):
        (self.root / "config/qemu.json").write_bytes(DEFAULT_CONFIG.read_bytes())

        def transport(config, image, case, **kwargs):
            folder = self.root / "build" / case
            folder.mkdir()
            stdout, stderr, report = folder / "stdout.log", folder / "stderr.log", folder / "report.json"
            stdout.write_text("unit fixture, not guest evidence")
            stderr.write_text("")
            report.write_text("{}")
            return {"status": "PASS", "stdout_path": str(stdout), "stderr_path": str(stderr), "tools": {}, "command": ["unit-fixture"]}, report

        def missing_observation(log):
            raise ValueError("fixture is missing its required record")

        with mock.patch("tools.gates.run_case", side_effect=transport), contextlib.redirect_stdout(io.StringIO()):
            passed = run_guest_cases("p0-image", "P0", {
                "first": lambda log: [{"name": "observed", "passed": True}], "second": missing_observation,
            }, self.raw, ["unit-fixture"], root=self.root)
        self.assertFalse(passed)
        receipt = json.loads((self.root / "build/reports/gates/p0-image.json").read_text())
        self.assertEqual(receipt["status"], "FAIL")
        self.assertTrue(any(item["name"] == "first:observed" and item["passed"] for item in receipt["assertions"]))
        self.assertTrue(any(item["name"] == "second:guest_observations_valid" and not item["passed"] for item in receipt["assertions"]))
        self.assertEqual(len(receipt["metadata"]["runs"]), 2)
        self.assertTrue((self.root / "build/second/semantic.json").is_file())

    def test_parse_dtc_platform(self):
        platform = dts_platform(DTS)
        self.assertEqual(platform["gicr"], 0x80A0000)
        self.assertEqual(platform["rtc_irq"], 34)
        self.assertEqual(platform["timer_irq"], 30)
        self.assertEqual(platform["ram_size"], 512 * 1024 * 1024)

    def test_duplicate_or_missing_dtc_device_is_rejected(self):
        with self.assertRaises(ValueError):
            dts_platform(DTS.replace('"arm,pl011"', '"unknown"'))
        with self.assertRaises(ValueError):
            dts_platform(DTS.replace('"arm,primecell";\n        reg = <0x0 0x9010000', '"arm,pl011";\n        reg = <0x0 0x9010000'))

    def test_guest_fields_are_numeric_unique_and_complete(self):
        self.assertEqual(guest_record("[BOOT] el=1 fdt=0x40000000 size=100\n", "BOOT", {"el", "fdt", "size"})["fdt"], 0x40000000)
        for log in ("[BOOT] el=1 el=2", "[BOOT] el=no", "[BOOT] el=1\n[BOOT] el=1", "[BOOT] fdt=1"):
            with self.subTest(log=log), self.assertRaises(ValueError):
                guest_record(log, "BOOT", {"el"})

    def test_host_gate_requires_executed_checks_without_sanitizer_diagnostics(self):
        self.assertTrue(all(item["passed"] for item in host_assertions("[HOST] pte=PASS assertions=25\n", "pte")))
        for log in ("", "[HOST] pte=PASS assertions=0\n", "[HOST] pte=PASS assertions=25 extra\n",
                    "[HOST] other=PASS assertions=25\n", "[HOST] pte=PASS assertions=25\n" * 2,
                    "[HOST] pte=PASS assertions=25\nruntime error: bad access\n"):
            with self.subTest(log=log):
                self.assertFalse(all(item["passed"] for item in host_assertions(log, "pte")))
        with self.assertRaises(ValueError):
            host_assertions("", "bad label")

    def test_p1_vm_observations_require_privilege_permissions_and_exception_return(self):
        log = "[VM] enabled=1 granule=4096 root=0x400a0000 high_pc=0xffffff8040080000 text_ro=1 data_xn=1 reserved=1\n[EXCEPTION] el=1 ec=60 recovered=1\n"
        self.assertTrue(all(item["passed"] for item in p1_vm_assertions(log)))
        for old, new in (("enabled=1", "enabled=0"), ("granule=4096", "granule=65536"), ("root=0x400a0000", "root=0x400a0001"),
                         ("high_pc=0xffffff8040080000", "high_pc=0x40080000"), ("text_ro=1", "text_ro=0"),
                         ("data_xn=1", "data_xn=0"), ("reserved=1", "reserved=0"), ("ec=60", "ec=36"), ("recovered=1", "recovered=0")):
            with self.subTest(old=old):
                self.assertFalse(all(item["passed"] for item in p1_vm_assertions(log.replace(old, new))))

    def test_isolation_checker_requires_el0_tasks_fault_target_and_survival(self):
        tasks = "".join(f"[TASK] role={role} el=0 instance=1\n" for role in range(3))
        for case, (ec, far) in P2_FAULTS.items():
            log = tasks + f"[ISOLATION] role=2 ec={ec} far=0x{far:x} svc=3 invalid_syscall=1 bad_pointer=1 continued=1\n"
            self.assertTrue(all(item["passed"] for item in p2_assertions(log, case)))
            for old, new in (("el=0", "el=1"), ("continued=1", "continued=0"), ("bad_pointer=1", "bad_pointer=0"),
                             (f"ec={ec}", "ec=0"), (f"far=0x{far:x}", f"far=0x{far + 1:x}")):
                with self.subTest(case=case, old=old):
                    self.assertFalse(all(item["passed"] for item in p2_assertions(log.replace(old, new), case)))

    def test_scheduler_checker_distinguishes_preemption_and_idle(self):
        log = "[SCHED] timer_irq=2 preemptions=1 sleep_wake=1 idle=1 invariants=1\n"
        self.assertTrue(all(item["passed"] for item in p3_assertions(log)))
        self.assertTrue(all(item["passed"] for item in p3_assertions(log, idle_case=True)))
        self.assertFalse(all(item["passed"] for item in p3_assertions(log.replace("preemptions=1", "preemptions=0"))))
        self.assertFalse(all(item["passed"] for item in p3_assertions(log.replace("idle=1", "idle=0"), idle_case=True)))

    def test_ipc_checker_accounts_for_completions_and_negative_paths(self):
        log = "[IPC] calls=2 replies=1 busy=1 timeout=1 stale=1 denied=1 completed=2 once=1\n"
        self.assertTrue(all(item["passed"] for item in p4_assertions(log)))
        for old, new in (("calls=2", "calls=3"), ("completed=2", "completed=1"), ("once=1", "once=0"),
                         ("busy=1", "busy=0"), ("stale=1", "stale=0"), ("denied=1", "denied=0")):
            with self.subTest(old=old):
                self.assertFalse(all(item["passed"] for item in p4_assertions(log.replace(old, new))))

    def test_rtc_checker_requires_observed_irq_or_timeout_path(self):
        log = "[RTC] alarm=1 irq=1 timeout=1 early=1 ack=1 ack_rejected=1\n"
        for case in ("p5-rtc", "p5-timeout", "p5-early"):
            self.assertTrue(all(item["passed"] for item in p5_assertions(log, case)))
        for case, old, new in (("p5-rtc", "irq=1", "irq=0"), ("p5-timeout", "timeout=1", "timeout=0"),
                               ("p5-early", "early=1", "early=0"), ("p5-rtc", "ack=1", "ack=2"),
                               ("p5-rtc", "ack_rejected=1", "ack_rejected=0")):
            with self.subTest(case=case):
                self.assertFalse(all(item["passed"] for item in p5_assertions(log.replace(old, new), case)))

    def test_client_timeout_keeps_service_cleanup_and_rejects_late_reply(self):
        log = "[RTC] alarm=1 irq=1 timeout=0 early=0 ack=1 ack_rejected=1\n"
        log += "[IPC] calls=3 replies=2 busy=1 timeout=1 stale=1 denied=0 completed=3 once=1\n"
        self.assertTrue(all(item["passed"] for item in p5_assertions(log, "p5-client-timeout")))
        for old, new in (("timeout=1", "timeout=0"), ("stale=1", "stale=0"),
                         ("busy=1", "busy=0"), ("ack=1", "ack=0"), ("completed=3", "completed=2")):
            with self.subTest(old=old):
                self.assertFalse(all(item["passed"] for item in p5_assertions(log.replace(old, new), "p5-client-timeout")))

    def test_restart_checker_enforces_iteration_floor_and_resource_baseline(self):
        log = "[TASK] role=1 el=0 instance=1\n"
        for instance in range(1, 101):
            log += f"[DEATH] role=1 instance={instance} epoch={instance + 1} esr=0x92000006 far=0x1000 masked=1\n"
            log += f"[REBUILD] instance={instance + 1} epoch={instance + 1} tables=28\n"
            log += f"[TASK] role=1 el=0 instance={instance + 1}\n"
        log += "[RESTART] count=100 peer_died=100 old_token=100 masked=100 resource_delta=0\n"
        self.assertTrue(all(item["passed"] for item in restart_assertions(log, 100)))
        for old, new in (("count=100", "count=99"), ("peer_died=100", "peer_died=0"),
                         ("old_token=100", "old_token=0"), ("masked=100", "masked=99"), ("resource_delta=0", "resource_delta=1"),
                         ("tables=28", "tables=0"), ("[DEATH] role=1 instance=1 ", "[DEATH] role=1 instance=7 ")):
            with self.subTest(old=old):
                self.assertFalse(all(item["passed"] for item in restart_assertions(log.replace(old, new), 100)))
        for changed in (log.replace("tables=28", "tables=29", 1), log.replace("masked=1\n", "masked=0\n", 1),
                        log.replace("[TASK] role=1 el=0 instance=101\n", ""),
                        log.replace("count=100", "count=18446744073709551615"),
                        log.replace("[REBUILD] instance=2 epoch=2 tables=28\n", "")):
            self.assertFalse(all(item["passed"] for item in restart_assertions(changed, 100)))

    def test_death_event_checker_rejects_wrong_identity_or_order(self):
        log = "[TASK] role=1 el=0 instance=1\n"
        for instance in range(1, 4):
            log += f"[DEATH] role=1 instance={instance} epoch={instance + 1} esr=0x92000006 far=0x1000 masked=1\n"
            if instance == 1:
                log += "[RESTART_WAIT] instance=1 epoch=2\n"
            log += f"[REBUILD] instance={instance + 1} epoch={instance + 1} tables=28\n"
            log += f"[TASK] role=1 el=0 instance={instance + 1}\n"
        log += "[RESTART] count=3 peer_died=3 old_token=3 masked=3 resource_delta=0\n"
        log += "[IPC] calls=10 replies=7 busy=0 timeout=0 stale=3 denied=0 completed=10 once=1\n"
        self.assertTrue(all(item["passed"] for item in pending_death_event_assertions(log)))
        wrong_id = log.replace("[RESTART_WAIT] instance=1", "[RESTART_WAIT] instance=2")
        self.assertFalse(all(item["passed"] for item in pending_death_event_assertions(wrong_id)))
        out_of_order = log.replace("[RESTART_WAIT] instance=1 epoch=2\n", "")
        out_of_order += "[RESTART_WAIT] instance=1 epoch=2\n"
        self.assertFalse(all(item["passed"] for item in pending_death_event_assertions(out_of_order)))

    def test_demo_accounts_for_peer_death_without_requiring_separate_negative_cases(self):
        log = "[TASK] role=1 el=0 instance=1\n"
        for instance in range(1, 4):
            log += f"[DEATH] role=1 instance={instance} epoch={instance + 1} esr=0x92000006 far=0x1000 masked=1\n"
            log += f"[REBUILD] instance={instance + 1} epoch={instance + 1} tables=28\n"
            log += f"[TASK] role=1 el=0 instance={instance + 1}\n"
        log += "[RESTART] count=3 peer_died=3 old_token=3 masked=3 resource_delta=0\n"
        log += "[IPC] calls=10 replies=7 busy=0 timeout=0 stale=3 denied=0 completed=10 once=1\n"
        log += "[RTC] alarm=1 irq=1 timeout=0 early=0 ack=1 ack_rejected=1\n"
        self.assertTrue(all(item["passed"] for item in demo_assertions(log)))
        self.assertFalse(all(item["passed"] for item in p4_assertions(log)))
        self.assertFalse(all(item["passed"] for item in demo_assertions(log.replace("completed=10", "completed=9"))))

    def test_pending_restart_requires_live_device_status_before_cleanup(self):
        log = (
            "[TASK] role=1 el=0 instance=1\n"
            "[DEATH] role=1 instance=1 epoch=2 esr=0x92000006 far=0x1000 masked=1 device_pending=1\n"
            "[REBUILD] instance=2 epoch=2 tables=28\n"
            "[TASK] role=1 el=0 instance=2\n"
            "[RESTART] count=1 peer_died=1 old_token=1 masked=1 resource_delta=0\n"
            "[IPC] calls=4 replies=3 busy=0 timeout=0 stale=1 denied=0 completed=4 once=1\n"
            "[RTC] alarm=2 irq=2 timeout=0 early=0 ack=1 ack_rejected=2\n"
        )
        self.assertTrue(all(item["passed"] for item in pending_restart_assertions(log)))
        for old, new in (("device_pending=1", "device_pending=0"), ("irq=2", "irq=1"),
                         ("ack=1", "ack=0"), ("ack_rejected=2", "ack_rejected=0")):
            with self.subTest(old=old):
                self.assertFalse(all(item["passed"] for item in pending_restart_assertions(log.replace(old, new))))
        with self.assertRaises(ValueError):
            pending_restart_assertions(log.replace(" device_pending=1", ""))


if __name__ == "__main__":
    unittest.main()
