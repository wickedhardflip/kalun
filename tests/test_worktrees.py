"""Sessions that ran in (or across) real linked git worktrees, built in a tempdir."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kalun import cli  # noqa: E402
from kalun.gitinfo import Git  # noqa: E402
from kalun.util import norm_path  # noqa: E402
from test_rules import Transcript, statuses  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


class WorktreeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp()).resolve()
        cls.main = cls.tmp / "my repo.v2"  # a space and a dot on purpose
        cls.main.mkdir()
        sh(cls.main, "init", "-q", "-b", "main")
        (cls.main / "a.py").write_text("x = 1\n")
        sh(cls.main, "add", ".")
        sh(cls.main, "commit", "-q", "-m", "first")
        cls.wt = cls.tmp / "worktrees" / "my repo.v2-feature"
        sh(cls.main, "worktree", "add", "-q", "-b", "feature", str(cls.wt))
        cls.sub = cls.tmp / "worktrees" / "my repo.v2-agent"
        sh(cls.main, "worktree", "add", "-q", "-b", "agent", str(cls.sub))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)  # only our own tempdir

    def test_dot_file_marks_a_linked_worktree(self):
        self.assertTrue((self.wt / ".git").is_file())

    def test_identity_in_main_and_linked(self):
        g = Git()
        m, w = g.snapshot(self.main), g.snapshot(self.wt)
        self.assertEqual((m["branch"], m["linked_worktree"]), ("main", False))
        self.assertEqual((w["branch"], w["linked_worktree"]), ("feature", True))
        self.assertEqual(m["common_dir"], w["common_dir"])
        self.assertEqual(w["repo_root"], norm_path(str(self.wt), lower=False))
        self.assertEqual(m["head"], w["head"])

    def test_every_git_call_works_in_a_linked_worktree(self):
        g = Git()
        wt = self.tmp / "worktrees" / "commits"
        sh(self.main, "worktree", "add", "-q", "-b", "commits", str(wt))
        head = g.head(wt)
        (wt / "b.py").write_text("y = 2\n")
        self.assertEqual(g.status(wt), ["b.py"])
        sh(wt, "add", ".")
        sh(wt, "commit", "-q", "-m", "second")
        new = g.head(wt)
        self.assertNotEqual(head, new)
        self.assertEqual(g.commits_between(wt, head, new), [new])
        self.assertEqual(g.changed_between(wt, head, new), ["b.py"])
        self.assertTrue(g.commit_exists(wt, new))
        self.assertEqual(g.head_at(wt, "2099-01-01T00:00:00Z"), new)
        self.assertTrue(g.commit_exists(self.main, new))  # commits are shared across worktrees

    def test_encode_cwd(self):
        self.assertEqual(cli.encode_cwd(r"D:\work\trees\repo-branch"), "D--work-trees-repo-branch")
        self.assertEqual(cli.encode_cwd(r"C:\a b\my.repo_v2"), "C--a-b-my-repo-v2")
        self.assertEqual(cli.encode_cwd("C:\\caf\u00e9"), "C--caf-")  # non-ASCII letters are replaced too

    def test_find_transcript_by_worktree_cwd(self):
        cfg = self.tmp / "cfg"
        folder = cfg / "projects" / cli.encode_cwd(self.wt)
        folder.mkdir(parents=True)
        (folder / "sess1.jsonl").write_text("{}\n")
        (cfg / "projects" / cli.encode_cwd(self.main)).mkdir(parents=True)
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
        try:
            self.assertEqual(cli.find_transcript(None, self.wt), folder / "sess1.jsonl")
            self.assertIsNone(cli.find_transcript(None, self.main))
            self.assertEqual(cli.find_transcript("sess1", self.main), folder / "sess1.jsonl")
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            if old is not None:
                os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_report_follows_recorded_cwd_not_process_cwd(self):
        t = Transcript()
        t.cwd = str(self.wt)
        t.prompt()
        t.say("Done.")
        here = os.getcwd()
        os.chdir(self.main)  # report run from the main checkout
        try:
            doc = t.doc(Git())
        finally:
            os.chdir(here)
        g = doc["git"]
        self.assertEqual((g["repo_root"], g["branch"], g["linked_worktree"]),
                         (norm_path(str(self.wt), lower=False), "feature", True))

    def test_hook_snapshot_from_one_worktree_is_used_by_report(self):
        out = self.tmp / "kalun-out"
        out.mkdir()
        snap = Git().snapshot(self.sub)
        import json
        (out / "git-before.json").write_text(json.dumps(snap))
        t = Transcript()
        t.cwd = str(self.main)  # transcript says main, the hook saw the agent worktree
        t.prompt()
        t.say("Done.")
        p = self.tmp / "t.jsonl"
        p.write_text("\n".join(t.lines) + "\n", encoding="utf-8")
        from kalun.report import build
        g = build(p, out, Git())["git"]
        self.assertEqual((g["repo_root"], g["branch"], g["head_before_source"]),
                         (snap["repo_root"], "agent", "hook"))

    def test_edit_path_resolves_against_the_editors_cwd(self):
        # A subagent in its own worktree edits a relative path; the parent claims the file by name.
        t = Transcript()
        t.cwd = str(self.main)
        t.prompt()
        t.cwd = str(self.sub)
        t.run("Edit", {"file_path": "a.py"}, "The file a.py has been updated successfully.")
        t.cwd = str(self.main)
        t.say("I updated a.py.")
        (kind, status, reason), = statuses(t.doc(Git()))
        self.assertEqual(kind, "file_change")
        self.assertEqual(status, "observed")
        self.assertEqual(reason, "tool_confirmed_only")

    def test_edit_in_other_worktree_is_not_confused_with_parent_file(self):
        t = Transcript()
        t.cwd = str(self.sub)
        t.prompt()
        t.run("Edit", {"file_path": "a.py"}, "The file a.py has been updated successfully.")
        t.cwd = str(self.main)
        t.say("I updated " + str(self.main / "a.py").replace("\\", "/") + ".")
        (kind, status, reason), = statuses(t.doc(Git()))
        self.assertEqual(status, "unverified")


if __name__ == "__main__":
    unittest.main()
