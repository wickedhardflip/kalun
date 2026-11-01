"""The repo is public-ready: no personal paths, names or session data in tracked files."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("briwa", "onedrive", "\\users\\", "/users/", ".claude/projects", "homelab", "e:\\projects", "e:/projects")


class Hygiene(unittest.TestCase):
    def test_no_personal_data(self):
        files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
        self.assertTrue(files, "run inside the git repo")
        for name in files:
            if name == "tests/test_hygiene.py" or name.endswith(".pyc"):
                continue
            text = (ROOT / name).read_text(encoding="utf-8", errors="ignore").lower()
            for bad in FORBIDDEN:
                self.assertFalse(bad in text, f"{name} contains {bad!r}")

    def test_no_session_data_outside_fixtures(self):
        files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
        for name in files:
            if name.endswith((".jsonl", "kalun.json", "receipt.md", "flight-log.md")):
                self.assertTrue(name.startswith("fixtures/"), f"{name} is session output outside fixtures/")
            self.assertNotIn(".kalun/", name)


if __name__ == "__main__":
    unittest.main()
