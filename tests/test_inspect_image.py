"""Raw Image fixtures are test inputs, not a bootable project kernel."""

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from tools.inspect_image import inspect_image


def image_bytes(**changes):
    fields = dict(code0=0x14000010, code1=0, text_offset=0x80000,
                  image_size=4096, flags=2, res2=0, res3=0, res4=0,
                  magic=0x644D5241, res5=0)
    fields.update(changes)
    return struct.pack("<IIQQQQQQII", *fields.values()) + b"\x00\x00\x00\x14"


class InspectImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fixture.img"

    def inspect(self, raw):
        self.path.write_bytes(raw)
        return inspect_image(self.path)

    def test_valid_header_and_payload(self):
        report = self.inspect(image_bytes())
        self.assertEqual(report["status"], "VALID")
        self.assertEqual(report["file_size"], 68)
        self.assertEqual(report["header"]["branch_target_offset"], 64)
        self.assertEqual(len(report["sha256"]), 64)

    def test_missing_or_truncated_image_is_invalid(self):
        self.assertEqual(inspect_image(self.path)["status"], "INVALID")
        self.assertEqual(self.inspect(b"\0" * 63)["status"], "INVALID")

    def test_directory_is_not_an_image(self):
        self.assertEqual(inspect_image(Path(self.directory.name))["status"], "INVALID")

    def test_rejects_wrong_magic_endianness_and_page_contract(self):
        for changes in ({"magic": 0x41524D64}, {"flags": 3}, {"flags": 4},
                        {"text_offset": 0}, {"res2": 1}, {"res5": 64}):
            with self.subTest(changes=changes):
                self.assertEqual(self.inspect(image_bytes(**changes))["status"], "INVALID")

    def test_rejects_understated_memory_and_overflow(self):
        for size in (0, 64, (1 << 64) - 1, (1 << 64) - 0x100000):
            with self.subTest(size=size):
                self.assertEqual(self.inspect(image_bytes(image_size=size))["status"], "INVALID")

    def test_rejects_branch_into_header_backwards_or_beyond_file(self):
        for branch in (0x14000000, 0x17FFFFFF, 0x14000011, 0x94000010, 0):
            with self.subTest(branch=branch):
                self.assertEqual(self.inspect(image_bytes(code0=branch))["status"], "INVALID")

    def test_cli_produces_machine_readable_failure(self):
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools/inspect_image.py"),
             str(self.path), "--json"], capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(process.returncode, 1)
        self.assertIn('"status": "INVALID"', process.stdout)
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
