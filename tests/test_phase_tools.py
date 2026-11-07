"""Synthetic receipts live only in temporary fixtures, never project gates."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.context import MAX_PACKET_CHARS, build_packet, render_packet
from tools.phase import main as phase_main, phase_statuses
from tools.project import file_sha256, load_manifest, resolve_project_path, source_fingerprint


class PhaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("docs/project-contract.md", "# Fixture contract\nFixed tasks; no runtime loader.\n")
        self.write("docs/phases.md", "# Fixture phase guide\n")
        self.write("docs/debugging.md", "# Fixture debugging\n")
        self.write("source.c", "int fixture;\n")
        self.manifest = {
            "schema_version": 1,
            "contract": "docs/project-contract.md",
            "phases": [
                {"id": "P0", "title": "Fixture boot", "depends_on": [], "gates": ["p0-boot"], "topics": ["boot"], "freeze": ["Fixture ABI"]},
                {"id": "P1", "title": "Fixture VM", "depends_on": ["P0"], "gates": ["p1-vm"], "topics": ["mmu"], "freeze": ["Fixture layout"]},
            ],
            "topics": {"boot": ["docs/phases.md"], "mmu": ["docs/debugging.md"]},
        }
        self.save_manifest()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def save_manifest(self):
        self.write("config/phases.json", json.dumps(self.manifest))

    def receipt(self, gate="p0-boot", phase="P0", *, save=True):
        evidence_path = self.write(f"build/reports/raw/{gate}.log", "SYNTHETIC FIXTURE ONLY\n")
        data = {
            "schema_version": 1, "gate_id": gate, "phase": phase,
            "status": "PASS", "evidence_kind": "phase_gate", "semantic_validation": True,
            "source_fingerprint": source_fingerprint(self.root),
            "command": ["fixture-validator", "--synthetic"], "exit_code": 0,
            "timestamp_utc": "2026-11-05T00:00:00Z",
            "assertions": [{"name": "synthetic fixture assertion", "passed": True}],
            "evidence": [{"path": evidence_path.relative_to(self.root).as_posix(), "sha256": file_sha256(evidence_path)}],
        }
        if save:
            self.save_receipt(gate, data)
        return data

    def save_receipt(self, gate, data):
        return self.write(f"build/reports/gates/{gate}.json", json.dumps(data))

    def statuses(self):
        return phase_statuses(root=self.root)

    def test_missing_receipts_are_not_run_and_no_flags_written(self):
        statuses = self.statuses()
        self.assertEqual([phase["status"] for phase in statuses], ["NOT_RUN", "NOT_RUN"])
        self.assertFalse(statuses[1]["prerequisites_satisfied"])
        self.assertFalse((self.root / "build").exists())

    def test_fresh_semantic_receipts_pass_with_dependencies(self):
        self.receipt()
        self.receipt("p1-vm", "P1")
        self.assertEqual([phase["status"] for phase in self.statuses()], ["PASS", "PASS"])

    def test_own_receipt_does_not_bypass_prerequisite(self):
        self.receipt("p1-vm", "P1")
        self.assertEqual(self.statuses()[1]["status"], "BLOCKED")

    def test_changed_source_invalidates_old_receipt(self):
        self.receipt()
        self.write("source.c", "int fixture_changed;\n")
        self.assertEqual(self.statuses()[0]["status"], "STALE")

    def test_source_addition_and_deletion_change_fingerprint(self):
        before = source_fingerprint(self.root)
        added = self.write("new.c", "int additional;\n")
        self.assertNotEqual(before, source_fingerprint(self.root))
        added.unlink()
        self.assertEqual(before, source_fingerprint(self.root))

    def test_outputs_and_interpreter_caches_do_not_change_fingerprint(self):
        before = source_fingerprint(self.root)
        for name in ("build/reports/result.json", "tools/__pycache__/x.pyc", ".git/index", "vendor/ext.c", "downloads/archive", ".venv/bin/python", ".tools-venv/settings"):
            self.write(name, "generated fixture")
        self.assertEqual(before, source_fingerprint(self.root))

    def test_fingerprint_does_not_follow_external_symlink(self):
        with tempfile.TemporaryDirectory() as external:
            external_file = Path(external) / "external.c"
            external_file.write_text("outside", encoding="utf-8")
            try:
                (self.root / "linked.c").symlink_to(external_file)
                (self.root / "linked-dir").symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Symlinks unavailable: {error}")
            before = source_fingerprint(self.root)
            external_file.write_text("outside changed", encoding="utf-8")
            self.assertEqual(before, source_fingerprint(self.root))

    def test_transport_pass_is_not_phase_pass(self):
        data = self.receipt(save=False)
        data.update(evidence_kind="qemu_transport", semantic_validation=False)
        self.save_receipt("p0-boot", data)
        self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_semantic_assertion_command_and_time_requirements(self):
        valid = self.receipt(save=False)
        mutations = [
            {"semantic_validation": False}, {"assertions": []},
            {"assertions": [{"name": "failed", "passed": False}]},
            {"assertions": [{"name": "numeric boolean", "passed": 1}]},
            {"assertions": [{"name": "same", "passed": True}, {"name": "same", "passed": True}]},
            {"command": []}, {"command": "fixture-validator"},
            {"exit_code": 1}, {"exit_code": False}, {"status": "FAIL"},
            {"timestamp_utc": "2026-11-05T00:00:00"},
            {"timestamp_utc": "2026-11-05T00:00:00+08:00"},
            {"timestamp_utc": "invalid"}, {"evidence": []},
            {"gate_id": "p1-vm"}, {"phase": "P1"},
            {"schema_version": True}, {"source_fingerprint": "not a digest"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(valid)
                candidate.update(mutation)
                self.save_receipt("p0-boot", candidate)
                self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_raw_evidence_change_is_rejected(self):
        self.receipt()
        self.write("build/reports/raw/p0-boot.log", "modified")
        self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_missing_raw_evidence_is_rejected(self):
        self.receipt()
        (self.root / "build/reports/raw/p0-boot.log").unlink()
        self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_evidence_traversal_and_absolute_paths_rejected(self):
        valid = self.receipt(save=False)
        for unsafe in ("../outside", "/etc/passwd", "build/../../outside", "C:/outside", "..\\outside"):
            with self.subTest(path=unsafe):
                data = copy.deepcopy(valid)
                data["evidence"][0]["path"] = unsafe
                self.save_receipt("p0-boot", data)
                self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_external_evidence_symlink_rejected(self):
        data = self.receipt(save=False)
        with tempfile.TemporaryDirectory() as external:
            evidence = Path(external) / "evidence.log"
            evidence.write_text("external fixture", encoding="utf-8")
            link = self.root / "build/reports/raw/link.log"
            try:
                link.symlink_to(evidence)
            except OSError as error:
                self.skipTest(f"Symlinks unavailable: {error}")
            data["evidence"] = [{"path": "build/reports/raw/link.log", "sha256": file_sha256(evidence)}]
            self.save_receipt("p0-boot", data)
            self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_malformed_and_oversized_receipts_rejected(self):
        self.write("build/reports/gates/p0-boot.json", "{")
        self.assertEqual(self.statuses()[0]["status"], "INVALID")
        self.write("build/reports/gates/p0-boot.json", " " * (1024 * 1024))
        self.assertEqual(self.statuses()[0]["status"], "INVALID")

    def test_invalid_manifests_rejected(self):
        mutations = [
            lambda data: data.update(schema_version=True),
            lambda data: data["phases"][1].update(id="P0"),
            lambda data: data["phases"][1].update(gates=["p0-boot"]),
            lambda data: data["phases"][0].update(depends_on=["P1"]),
            lambda data: data["phases"][1].update(depends_on=["P1"]),
            lambda data: data["phases"][1].update(topics=["unknown"]),
            lambda data: data["topics"].update(boot=["missing.md"]),
            lambda data: data.update(contract="../outside"),
        ]
        original = copy.deepcopy(self.manifest)
        for mutation in mutations:
            self.manifest = copy.deepcopy(original)
            mutation(self.manifest)
            self.save_manifest()
            with self.subTest(manifest=self.manifest), self.assertRaises(ValueError):
                load_manifest(self.root)

    def test_report_directory_escape_rejected(self):
        with self.assertRaises(ValueError):
            phase_statuses(root=self.root, reports="../outside")

    def test_context_selects_phase_without_emitting_old_html(self):
        self.write("microkernel_os_build_spec.html", "OLD_HTML_SENTINEL" * 50000)
        packet = build_packet("P1", root=self.root)
        output = render_packet(packet)
        self.assertIn("P1 - Fixture VM", output)
        self.assertIn("python3 tools/phase.py check P0", output)
        self.assertIn("docs/debugging.md", packet["read_next"])
        self.assertNotIn("OLD_HTML_SENTINEL", output)
        self.assertLessEqual(len(output), MAX_PACKET_CHARS)
        self.assertFalse((self.root / "build").exists())

    def test_context_large_contract_is_explicitly_indexed_not_truncated(self):
        self.write("docs/project-contract.md", "large contract\n" * 2000)
        packet = build_packet("P0", root=self.root)
        output = render_packet(packet)
        self.assertIsNone(packet["contract"])
        self.assertIn("not truncated", output)
        self.assertIn("make check", output)
        self.assertLessEqual(len(output), MAX_PACKET_CHARS)

    def test_context_unknown_phase_or_topic_rejected(self):
        with self.assertRaises(ValueError):
            build_packet("P99", root=self.root)
        with self.assertRaises(ValueError):
            build_packet("P0", topic="unknown", root=self.root)

    def test_cli_check_exit_code_tracks_receipts(self):
        with patch("tools.phase.ROOT", self.root):
            # main calls phase_statuses with its default ROOT; inject only root.
            with patch("tools.phase.phase_statuses", side_effect=lambda **kwargs: phase_statuses(root=self.root, **kwargs)):
                with patch("builtins.print"):
                    self.assertEqual(phase_main(["check", "P0"]), 1)
                    self.receipt()
                    self.assertEqual(phase_main(["check", "P0"]), 0)
                    self.assertEqual(phase_main(["check", "P99"]), 2)


if __name__ == "__main__":
    unittest.main()
