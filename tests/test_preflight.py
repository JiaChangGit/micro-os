from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools import preflight


class PreflightTests(unittest.TestCase):
    @patch.object(preflight.platform, "system", return_value="Linux")
    @patch.object(preflight, "resource_info", return_value={})
    @patch.object(preflight.shutil, "which", return_value=None)
    @patch.object(preflight, "run_probe")
    def test_missing_tools_block_target_without_running_qemu(self, probe, which, resource, system):
        report = preflight.inspect_environment("target")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["tools"]["qemu-system-aarch64"]["status"], "MISSING")
        self.assertTrue(report["blockers"])
        probe.assert_not_called()

    @patch.object(preflight.platform, "system", return_value="Linux")
    @patch.object(preflight, "resource_info", return_value={})
    @patch.object(preflight.shutil, "which", side_effect=lambda name: "/tools/" + name)
    @patch.object(preflight, "run_probe", return_value={"status": "OK", "output": "version 1", "output_truncated": False})
    def test_host_profile_does_not_need_target_dependencies(self, probe, which, resource, system):
        report = preflight.inspect_environment("host")
        self.assertEqual(report["status"], "READY")
        self.assertNotIn("qemu-system-aarch64", report["tools"])
        self.assertEqual(report["capabilities"], {})

    @patch.object(preflight.platform, "system", return_value="Linux")
    @patch.object(preflight, "resource_info", return_value={})
    @patch.object(preflight.shutil, "which", side_effect=lambda name: "/tools/" + name)
    def test_installed_tools_without_required_qemu_capabilities_still_block(self, which, resource, system):
        def probe(command):
            output = "x86_64-linux-gnu" if command[-1] == "-dumpmachine" else "version 1"
            return {"status": "OK", "output": output, "output_truncated": False}
        with patch.object(preflight, "run_probe", side_effect=probe):
            report = preflight.inspect_environment("target")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["capabilities"]["virt_machine"]["supported"])
        self.assertFalse(report["capabilities"]["compiler_target"]["supported"])

    @patch.object(preflight.platform, "system", return_value="Linux")
    @patch.object(preflight, "resource_info", return_value={})
    @patch.object(preflight.shutil, "which", side_effect=lambda name: "/tools/" + name)
    def test_valid_target_tools_and_help_are_ready_without_kvm(self, which, resource, system):
        def probe(command):
            output = "version 1"
            if command[-2:] == ["-machine", "help"]:
                output = "Supported machines:\nvirt  ARM Virtual Machine\n"
            elif command[-2:] == ["-machine", "virt,help"]:
                output = "gic-version virtualization highmem"
            elif command[-2:] == ["-cpu", "help"]:
                output = "Available CPUs:\ncortex-a53\n"
            elif command[-1] == "-dumpmachine":
                output = "aarch64-linux-gnu\n"
            return {"status": "OK", "output": output, "output_truncated": False}
        with patch.object(preflight, "run_probe", side_effect=probe):
            report = preflight.inspect_environment("target")
        self.assertEqual(report["status"], "READY")
        self.assertTrue(all(item["supported"] for item in report["capabilities"].values()))

    def test_probe_reports_nonzero_exit(self):
        report = preflight.run_probe([sys.executable, "-c", "import sys; print('diagnostic'); sys.exit(7)"])
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["exit_code"], 7)
        self.assertIn("diagnostic", report["output"])

    def test_probe_bounds_output(self):
        report = preflight.run_probe([sys.executable, "-c", "print('x' * 100000)"])
        self.assertTrue(report["output_truncated"])
        self.assertEqual(len(report["output"]), preflight.OUTPUT_LIMIT)

    def test_probe_times_out_instead_of_hanging(self):
        report = preflight.run_probe([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)
        self.assertEqual(report["status"], "TIMEOUT")

    def test_probe_missing_executable_is_a_report(self):
        report = preflight.run_probe(["/nonexistent/micro-os-probe"])
        self.assertEqual(report["status"], "ERROR")
        self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main()
