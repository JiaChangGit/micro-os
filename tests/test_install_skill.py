"""Installation behavior; all targets are temporary fixture directories."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.install_skill import MANIFEST, NAME, synchronize


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        (self.source / "agents").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("skill version one\n")
        (self.source / "agents/openai.yaml").write_text("interface: {}\n")
        self.target = self.base / "installed"

    def test_install_check_and_idempotence(self):
        self.assertEqual(synchronize(self.source, self.target, check=True)["status"], "OUT_OF_DATE")
        self.assertFalse(self.target.exists())
        self.assertEqual(synchronize(self.source, self.target)["status"], "INSTALLED")
        self.assertEqual(synchronize(self.source, self.target)["status"], "CURRENT")
        self.assertEqual(synchronize(self.source, self.target, check=True)["status"], "CURRENT")

    def test_update_and_remove_only_previously_managed_files(self):
        (self.source / "old.md").write_text("old")
        synchronize(self.source, self.target)
        destination = self.target / NAME
        (destination / "personal.md").write_text("keep")
        (self.source / "old.md").unlink()
        (self.source / "SKILL.md").write_text("version two")
        self.assertEqual(synchronize(self.source, self.target)["status"], "INSTALLED")
        self.assertEqual((destination / "SKILL.md").read_text(), "version two")
        self.assertFalse((destination / "old.md").exists())
        self.assertEqual((destination / "personal.md").read_text(), "keep")

    def test_local_modification_refuses_before_any_write(self):
        synchronize(self.source, self.target)
        destination = self.target / NAME
        (destination / "SKILL.md").write_text("local changes")
        (self.source / "agents/openai.yaml").write_text("new UI")
        with self.assertRaisesRegex(ValueError, "locally modified"):
            synchronize(self.source, self.target)
        self.assertEqual((destination / "agents/openai.yaml").read_text(), "interface: {}\n")

    def test_unmanaged_target_is_preserved(self):
        destination = self.target / NAME
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("unrelated skill")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            synchronize(self.source, self.target)
        self.assertFalse((destination / MANIFEST).exists())

    def test_malicious_manifest_cannot_escape(self):
        synchronize(self.source, self.target)
        manifest = self.target / NAME / MANIFEST
        manifest.write_text(json.dumps({"schema_version": 1, "name": NAME, "files": {"../outside": "bad"}}))
        with self.assertRaisesRegex(ValueError, "invalid managed path"):
            synchronize(self.source, self.target)

    def test_symlink_cannot_redirect_installation(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.target.mkdir()
        (self.target / NAME).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            synchronize(self.source, self.target)
        self.assertEqual(list(outside.iterdir()), [])

    def test_new_source_cannot_overwrite_untracked_file(self):
        synchronize(self.source, self.target)
        (self.target / NAME / "notes.md").write_text("personal")
        (self.source / "notes.md").write_text("new source")
        with self.assertRaisesRegex(ValueError, "unmanaged file"):
            synchronize(self.source, self.target)


if __name__ == "__main__":
    unittest.main()
