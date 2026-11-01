"""Golden tests: each fixtures/<name>/transcript.jsonl must produce the committed outputs.

Run:      python -m unittest discover -s tests
Refresh:  KALUN_UPDATE=1 python -m unittest discover -s tests   (then read the diff)
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kalun.gitinfo import Git  # noqa: E402
from kalun.render import flight_log, receipt  # noqa: E402
from kalun.report import build, dump  # noqa: E402

FIXTURES = ROOT / "fixtures"


class NoGit(Git):
    """Fixtures run without touching any real repo."""

    def run(self, repo, *args):
        return None


def render_all(fixture):
    out = Path(tempfile.mkdtemp())
    try:
        doc = build(fixture / "transcript.jsonl", out, NoGit())
    finally:
        shutil.rmtree(out, ignore_errors=True)
    doc["session"]["transcript_path"] = "transcript.jsonl"  # machine independent
    return {"expected.json": dump(doc), "flight-log.md": flight_log(doc), "receipt.md": receipt(doc)}


class FixtureTests(unittest.TestCase):
    def test_fixtures(self):
        names = sorted(p.name for p in FIXTURES.iterdir() if (p / "transcript.jsonl").exists())
        self.assertTrue(names, "no fixtures found")
        for name in names:
            with self.subTest(fixture=name):
                got = render_all(FIXTURES / name)
                for fname, text in got.items():
                    path = FIXTURES / name / fname
                    if os.environ.get("KALUN_UPDATE"):
                        path.write_text(text, encoding="utf-8", newline="\n")
                    self.assertEqual(path.read_text(encoding="utf-8"), text, f"{name}/{fname}")

    def test_deterministic(self):
        f = FIXTURES / "s3_edit_then_commit"
        self.assertEqual(render_all(f), render_all(f))


if __name__ == "__main__":
    unittest.main()
