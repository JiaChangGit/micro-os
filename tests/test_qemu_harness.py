"""Exercise host transport with temporary fake processes, never a guest kernel."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from tools.qemu_command import DEFAULT_CONFIG, build_qemu_command, load_config, validate_config
from tools.run_qemu import main, report_base, run_case, valid_timeout


CONFIG = {
    "schema_version": 1, "executable": "qemu-system-aarch64", "machine": "virt",
    "cpu": "cortex-a53", "memory_mib": 512, "smp": 1, "rtc_clock": "vm",
    "machine_properties": {"gic-version": 3, "virtualization": "off", "highmem": "off"},
}


class CommandTests(unittest.TestCase):
    def test_repository_default_config_builds_the_real_command(self):
        command = build_qemu_command(load_config(DEFAULT_CONFIG), "build/kernel.img")
        self.assertIn("virt,gic-version=3,virtualization=off,highmem=off", command)

    def test_test_profile_and_unsupported_release_profile(self):
        command = build_qemu_command(CONFIG, "image with spaces.img")
        self.assertIn("image with spaces.img", command)
        self.assertEqual(command[command.index("-accel") + 1], "tcg")
        self.assertIn("base=utc,clock=vm", command)
        self.assertIn("enable=on,target=native,userspace=off", command)
        with self.assertRaises(ValueError):
            build_qemu_command(CONFIG, "image", profile="release")

    def test_debug_binds_only_loopback(self):
        command = build_qemu_command(CONFIG, "image", profile="debug", gdb_port=2345)
        self.assertIn("-S", command)
        self.assertIn("tcp:127.0.0.1:2345", command)
        self.assertIn("enable=on,target=native,userspace=off", command)
        with self.assertRaises(ValueError):
            build_qemu_command(CONFIG, "image", profile="debug", gdb_port=0)

    def test_boot_arguments_are_one_bounded_argv_value(self):
        command = build_qemu_command(CONFIG, "image", boot_args="case=p2-isolation")
        self.assertEqual(command[command.index("-append") + 1], "case=p2-isolation")
        self.assertNotIn("-append", build_qemu_command(CONFIG, "image"))
        self.assertIn("x" * 63, build_qemu_command(CONFIG, "image", boot_args="x" * 63))
        for value in (None, False, 0, "x" * 64, "case=bad\0arg", "case=bad\narg", "case=中文"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_qemu_command(CONFIG, "image", boot_args=value)

    def test_reject_unsupported_platform_settings(self):
        for key, value in (("schema_version", True), ("smp", 2), ("smp", True), ("rtc_clock", "host"),
                           ("cpu", "max"), ("memory_mib", 256), ("memory_mib", 1024), ("machine", "virt,secure=on")):
            with self.subTest(key=key, value=value):
                config = copy.deepcopy(CONFIG)
                config[key] = value
                with self.assertRaises(ValueError):
                    validate_config(config)

    def test_timeout_requires_finite_positive_number(self):
        for value in (0, -1, "nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                valid_timeout(value)


@unittest.skipUnless(sys.platform == "linux", "Process lifecycle tests require Linux /proc")
class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="micro-os-harness-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "kernel image.img"
        self.image.write_bytes(b"test-only transport fixture, not a bootable image")
        self.executable = self.root / "fake-qemu"
        self.config_path = self.root / "qemu.json"
        self.config_path.write_text(json.dumps(CONFIG), encoding="utf-8")

    def fake_qemu(self, body: str):
        self.executable.write_text(
            f"#!{sys.executable}\n"
            "import os, signal, subprocess, sys, time\n"
            "if '--version' in sys.argv:\n"
            "    print('Fake QEMU transport fixture 1.0')\n"
            "    sys.exit(0)\n" + textwrap.dedent(body), encoding="utf-8",
        )
        self.executable.chmod(0o755)

    def run_fixture(self, **kwargs):
        return run_case(CONFIG, self.image, "Q01", project_root=self.root,
                        executable=str(self.executable), **kwargs)

    def test_success_preserves_raw_logs_and_transport_boundary(self):
        self.fake_qemu("""
            print('[TEST] CASE=Q01 START')
            print('[PROBE] current_el=1')
            print('[TEST] CASE=Q01 PASS')
        """)
        report, path = self.run_fixture(markers=["[PROBE] current_el=1"])
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["semantic_validation"])
        self.assertEqual(report["evidence_kind"], "qemu_transport")
        self.assertEqual(json.loads(path.read_text())["status"], "PASS")
        self.assertEqual(len(report["image"]["sha256"]), 64)
        self.assertTrue(all(item["passed"] for item in report["assertions"]))
        self.assertIn("current_el=1", Path(report["stdout_path"]).read_text())
        _, second_path = self.run_fixture()
        self.assertNotEqual(path, second_path)

    def test_marker_cannot_hide_nonzero_exit(self):
        self.fake_qemu("""
            print('[TEST] CASE=Q01 START')
            print('[TEST] CASE=Q01 PASS')
            sys.exit(7)
        """)
        report, _ = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["exit_code"], 7)

    def test_boot_arguments_reach_guest_and_raw_command(self):
        self.fake_qemu("""
            if '-append' not in sys.argv or sys.argv[sys.argv.index('-append') + 1] != 'case=Q01':
                sys.exit(2)
            print('[TEST] CASE=Q01 START')
            print('[TEST] CASE=Q01 PASS')
        """)
        report, _ = self.run_fixture(boot_args="case=Q01")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["command"][report["command"].index("-append") + 1], "case=Q01")

    def test_missing_or_nonexact_marker_fails(self):
        for line in ("[TEST] CASE=Q02 PASS", "[TEST] CASE=Q01 PASS extra", ""):
            with self.subTest(line=line):
                self.fake_qemu(f"print('[TEST] CASE=Q01 START')\nprint({line!r})\n")
                report, _ = self.run_fixture()
                self.assertEqual(report["status"], "FAIL")

    def test_panic_and_invariant_failure_on_stderr_fail(self):
        for line in ("[PANIC] bad trap", "Kernel panic: bad trap", "[INVARIANT] INV-01 failed", "[TEST] CASE=Q01 FAIL"):
            with self.subTest(line=line):
                self.fake_qemu(
                    "print('[TEST] CASE=Q01 START')\n"
                    "print('[TEST] CASE=Q01 PASS')\n"
                    f"print({line!r}, file=sys.stderr)\n"
                )
                report, _ = self.run_fixture()
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(report["observed"]["failure_lines"])

    def test_large_output_does_not_block_pipe(self):
        self.fake_qemu("""
            os.write(1, (b'x' * 200 + b'\\n') * 1000)
            os.write(2, (b'y' * 200 + b'\\n') * 1000)
            print('[TEST] CASE=Q01 START')
            print('[TEST] CASE=Q01 PASS')
        """)
        report, _ = self.run_fixture(timeout=3)
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(Path(report["stdout_path"]).stat().st_size, 200000)

    def test_unbounded_guest_line_fails_without_hiding_next_markers(self):
        self.fake_qemu("""
            os.write(1, b'x' * 1000000 + b'\\n')
            print('[TEST] CASE=Q01 START')
            print('[TEST] CASE=Q01 PASS')
        """)
        report, _ = self.run_fixture(timeout=3)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(all(report["observed"]["markers"].values()))
        self.assertIn("exceeds 4096 bytes", report["observed"]["failure_lines"][0])

    def test_missing_image_and_executable_are_blocked(self):
        self.fake_qemu("print('unused')")
        self.image.unlink()
        report, path = self.run_fixture()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIsNone(report["exit_code"])
        self.assertTrue(path.is_file())
        self.image.write_bytes(b"fixture")
        self.executable.unlink()
        report, _ = self.run_fixture()
        self.assertEqual(report["status"], "BLOCKED")

    def test_signal_exit_is_not_success(self):
        self.fake_qemu("os.kill(os.getpid(), signal.SIGTERM)")
        report, _ = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["exit_code"], -signal.SIGTERM)

    def test_interrupt_during_version_does_not_start_guest(self):
        self.fake_qemu("raise RuntimeError('guest must not start')")
        with mock.patch("tools.run_qemu.qemu_version", side_effect=KeyboardInterrupt):
            report, _ = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["interrupted"])
        self.assertIsNone(report["exit_code"])

    def test_sigterm_to_cli_cleans_guest_and_writes_failure(self):
        pid_path = self.root / "guest.pid"
        self.fake_qemu(f"""
            open({str(pid_path)!r}, 'w').write(str(os.getpid()))
            time.sleep(20)
        """)
        wrapper = (
            "from pathlib import Path; import tools.run_qemu as harness; "
            f"harness.PROJECT_ROOT=Path({str(self.root)!r}); "
            f"raise SystemExit(harness.main(['--config',{str(self.config_path)!r},"
            f"'--image',{str(self.image)!r},'--case','Q01','--executable',{str(self.executable)!r}]))"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", wrapper], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 3
            while not pid_path.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pid_path.exists(), "fixture guest did not start")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 1, stderr)
            report = json.loads(Path(json.loads(stdout)["report"]).read_text())
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(report["interrupted"])
            self.assertFalse(report["surviving_processes"])
            self.assertFalse(Path(f"/proc/{int(pid_path.read_text())}").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    def test_timeout_kills_process_group(self):
        pid_path = self.root / "child.pid"
        self.fake_qemu(f"""
            child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)'])
            open({str(pid_path)!r}, 'w').write(str(child.pid))
            time.sleep(20)
        """)
        report, _ = self.run_fixture(timeout=0.2)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["timed_out"])
        self.assertFalse(report["surviving_processes"])
        child_pid = int(pid_path.read_text())
        if Path(f"/proc/{child_pid}/stat").exists():
            state = Path(f"/proc/{child_pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            self.assertIn(state, ("Z", "X"))

    def test_parent_exit_cannot_leave_live_descendant(self):
        self.fake_qemu("""
            subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)'])
            print('[TEST] CASE=Q01 START')
            print('[TEST] CASE=Q01 PASS')
        """)
        report, _ = self.run_fixture(timeout=2)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["unexpected_descendants"])
        self.assertFalse(report["surviving_processes"])

    def test_reject_path_escape_and_invalid_case_before_writing(self):
        for directory in ("../escape", "build/../escape", self.root.parent / "escape"):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                self.run_fixture(report_dir=directory)
        for case in ("../Q01", "/Q01", "Q01/sub", "Q01 PASS", ""):
            with self.subTest(case=case), self.assertRaises(ValueError):
                run_case(CONFIG, self.image, case, project_root=self.root)
        self.assertFalse((self.root / "build").exists())

    def test_report_root_symlink_escape_is_rejected(self):
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            report_base("escape/evidence", self.root)

    def test_case_parent_symlink_escape_is_rejected(self):
        base = self.root / "build" / "reports" / "qemu"
        base.mkdir(parents=True)
        (base / "Q01").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_fixture()

    def test_dry_run_does_not_write_reports_or_require_image(self):
        with mock.patch("tools.run_qemu.PROJECT_ROOT", self.root), contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(["--config", str(self.config_path), "--image", "missing.img", "--case", "Q01", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertIn("-kernel", json.loads(output.getvalue())["command"])
        self.assertFalse((self.root / "build").exists())


if __name__ == "__main__":
    unittest.main()
