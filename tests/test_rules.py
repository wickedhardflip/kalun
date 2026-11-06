"""Rule tests on tiny synthetic transcripts and a stubbed git."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kalun.claims import extract  # noqa: E402
from kalun.gitinfo import Git  # noqa: E402
from kalun.reader import classify, progress_only_ok, read  # noqa: E402
from kalun.report import build  # noqa: E402
from kalun.util import fmt_et, norm_path, scrub  # noqa: E402

CWD = "C:\\repo"


class StubGit(Git):
    """Answers read-only git calls from a dict keyed by subcommand."""

    def __init__(self, **answers):
        self.answers = answers

    def run(self, repo, *args):
        return self.answers.get(args[0])


class Transcript:
    def __init__(self):
        self.lines, self.n, self.cwd = [], 0, CWD

    def _rec(self, typ, content, **extra):
        self.n += 1
        rec = dict(type=typ, uuid=f"u{self.n}", sessionId="s1", cwd=self.cwd, version="2.0",
                   timestamp=f"2026-10-01T12:00:{self.n:02d}.000Z", gitBranch="main",
                   message=dict(id=f"m{self.n}", role=typ, content=content), **extra)
        self.lines.append(json.dumps(rec))
        return rec

    def prompt(self, text="go"):
        self._rec("user", text)

    def say(self, text):
        self._rec("assistant", [dict(type="text", text=text)])

    def run(self, tool, inp, result, is_error=False):
        tid = f"t{self.n}"
        self._rec("assistant", [dict(type="tool_use", id=tid, name=tool, input=inp)])
        self._rec("user", [dict(type="tool_result", tool_use_id=tid, content=result, is_error=is_error)])

    def write(self, p, subs=None):
        """Write the transcript to p; subs maps (agent id, parent tool_use id) to a subagent Transcript."""
        p.write_bytes(("\n".join(self.lines) + "\n").encode())  # LF on every OS, the golden hash depends on it
        for (aid, tool_use_id), sub in (subs or {}).items():
            d = p.with_suffix("") / "subagents"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"agent-{aid}.jsonl").write_bytes(("\n".join(sub.lines) + "\n").encode())
            (d / f"agent-{aid}.meta.json").write_text(json.dumps(dict(toolUseId=tool_use_id)), encoding="utf-8")

    def doc(self, git=None, subs=None):
        d = Path(tempfile.mkdtemp())
        p = d / "t.jsonl"
        self.write(p, subs)
        return build(p, d, git or StubGit())


def statuses(doc):
    return [(c["type"], c["status"], c["reason"]) for c in doc["claims"]]


class Rules(unittest.TestCase):
    def test_failed_run_then_pass_claim(self):
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "pytest -q 2>&1 | tail -5"}, "FAILED tests/test_a.py::test_x\n1 failed, 3 passed")
        t.say("All tests pass.")
        self.assertEqual(statuses(t.doc()), [("tests_pass", "unverified", "last_run_failed")])

    def test_no_run_and_no_marker(self):
        t = Transcript()
        t.prompt()
        t.say("The tests pass.")
        self.assertEqual(statuses(t.doc()), [("tests_pass", "unverified", "no_test_run")])
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "pytest -q"}, "")
        t.say("The tests pass.")
        self.assertEqual(statuses(t.doc()), [("tests_pass", "partial", "no_result_marker")])

    def test_edit_after_run_makes_it_partial(self):
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "pytest -q"}, "5 passed in 0.1s")
        t.run("Edit", {"file_path": "C:\\repo\\a.py"}, "The file C:\\repo\\a.py has been updated successfully.")
        t.say("The tests pass.")
        self.assertEqual(statuses(t.doc())[0][1:], ("partial", "files_changed_after_run"))

    def test_python_dash_m_unittest_counts_as_a_test_run(self):
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "python -m unittest discover -s tests 2>&1 | tail -3"}, "Ran 13 tests in 0.04s\n\nOK")
        t.say("All 13 tests pass.")
        self.assertEqual(statuses(t.doc())[0][:2], ("tests_pass", "observed"))

    def test_markdown_noise_is_stripped_from_claim_text(self):
        t = Transcript()
        t.prompt()
        t.say("- **Tests:** the tests pass.")
        self.assertEqual(t.doc()["claims"][0]["text"], "Tests: the tests pass.")

    def test_hedged_sentences_make_no_claim(self):
        t = Transcript()
        t.prompt()
        t.say("I'll commit once the tests pass. Main has uncommitted changes. It isn't pushed yet.")
        self.assertEqual(t.doc()["claims"], [])

    def test_exception_caps_at_partial(self):
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "pytest -q"}, "5 passed in 0.1s")
        t.say("All tests pass except the slow one.")
        self.assertEqual(statuses(t.doc())[0][1:], ("partial", "claim_has_exception"))

    def test_commit_checked_against_head(self):
        for head_after, want in (("bbb", ("observed", "head_moved")), ("aaa", ("unverified", "head_unchanged"))):
            t = Transcript()
            t.prompt()
            t.run("Bash", {"command": 'git commit -m "x"'}, "[main bbb1234] x")
            t.say("I committed the change.")
            git = StubGit(**{"rev-parse": "C:/repo\n", "rev-list": "bbb1234\n", "status": "", "diff": ""})
            git.run = lambda repo, *a, h=head_after: (
                "C:/repo\n" if a[:2] == ("rev-parse", "--show-toplevel") else
                h + "\n" if a[0] == "rev-parse" else "aaa\n" if a[0] == "rev-list" and "--before" in " ".join(a)
                else "bbb1234\n" if a[0] == "rev-list" else "" if a[0] in ("status", "diff") else None)
            doc = t.doc(git)
            self.assertEqual(statuses(doc)[0][1:], want)

    def _claims(self, *sentences):
        t = Transcript()
        t.prompt()
        for s in sentences:
            t.say(s)
        return [(c["type"], c["subject"]) for c in t.doc()["claims"]]

    def test_more_phrasings_make_claims(self):
        self.assertEqual(self._claims("I implemented src/app.py."), [("file_change", "src/app.py")])
        self.assertEqual(self._claims("README.md was updated."), [("file_change", "README.md")])
        self.assertEqual(self._claims("config.json has been rewritten."), [("file_change", "config.json")])
        self.assertEqual(self._claims("The pytest suite passes."), [("tests_pass", None)])
        self.assertEqual(self._claims("The build completed successfully."), [("build_ok", None)])
        self.assertEqual(self._claims("tsc passes cleanly."), [("build_ok", None)])
        self.assertEqual(self._claims("Committed abc1234."), [("committed", "abc1234")])
        self.assertEqual(self._claims("Committed as commit abc1234."), [("committed", "abc1234")])

    def test_strictness_holds(self):
        for s in ("Your settings.json is untouched.", "I haven't updated README.md.",
                  "I'll update README.md next.", "Once the tests pass I will commit.",
                  "It would be built after review.", "Use e.g. a flag.",
                  "Any committed files are listed below.", "The build might pass."):
            self.assertEqual(self._claims(s), [], s)

    def test_path_matching_across_styles(self):
        cases = (("C:\\repo\\src\\a.py", "src/a.py"), ("C:\\repo\\a.py", "a.py"), ("/c/repo/a.py", "C:/repo/a.py"),
                 ("C:\\repo\\a.py", "C:\\repo\\a.py"), ("C:\\repo\\.claude\\settings.json", ".claude/settings.json"),
                 ("C:\\repo\\a.py", "./a.py"))
        for edited, said in cases:
            t = Transcript()
            t.prompt()
            t.run("Write", {"file_path": edited}, "File created successfully")
            t.say(f"I updated {said}.")
            self.assertEqual(statuses(t.doc())[0][1:], ("observed", "tool_confirmed_only"), (edited, said))

    def test_same_basename_in_two_dirs_is_ambiguous(self):
        t = Transcript()
        t.prompt()
        for d in ("a", "b"):
            t.run("Write", {"file_path": f"C:\\repo\\{d}\\x.py"}, "ok")
        t.say("I updated x.py.")
        self.assertEqual(statuses(t.doc())[0][1:], ("partial", "ambiguous_path"))

    def test_git_happy_path_commit_and_file(self):
        t = Transcript()
        t.prompt()
        t.run("Edit", {"file_path": "C:\\repo\\src\\a.py"}, "The file C:\\repo\\src\\a.py has been updated successfully.")
        t.run("Bash", {"command": 'git commit -am "x"'}, "[main bbb1234] x\n 1 file changed")
        t.say("I updated src/a.py and committed it as bbb1234.")
        git = StubGit()
        git.run = lambda repo, *a: (
            "C:/repo\n" if a[:2] == ("rev-parse", "--show-toplevel") else
            "bbb1234ffff\n" if a[0] == "rev-parse" else
            "aaa0000\n" if a[0] == "rev-list" and any("--before" in x for x in a) else
            "bbb1234ffff\n" if a[0] == "rev-list" else
            "src/a.py\n" if a[0] == "diff" else "" if a[0] == "status" else None)
        self.assertEqual(sorted(statuses(t.doc(git))), [("committed", "observed", "head_moved"),
                                                         ("file_change", "observed", "tool_and_git")])

    def test_denied_never_counts(self):
        t = Transcript()
        t.prompt()
        t.run("Bash", {"command": "git commit -m x"}, "Permission for this action was denied by the classifier", True)
        t.say("I committed it.")
        doc = t.doc()
        self.assertEqual(statuses(doc)[0][1:], ("unverified", "no_commit_command"))
        self.assertIn("denied", [n["code"] for n in doc["notes"]])

    def test_outcome_rules(self):
        self.assertEqual(classify("Exit code 1\nboom", False, False), "failed")
        self.assertEqual(classify("ok", True, False), "failed")
        self.assertEqual(classify("12 passed, 1 failed", False, False), "failed")
        self.assertEqual(classify("3 passed in 1s", False, False), "ok")
        self.assertEqual(classify("done", False, False), "unknown")
        self.assertTrue(progress_only_ok("....\n..   [100%]"))
        self.assertFalse(progress_only_ok("..F.  [ 50%]\n..    [100%]"))

    def test_bad_lines_are_counted(self):
        d = Path(tempfile.mkdtemp()) / "t.jsonl"
        d.write_text("not json\n", encoding="utf-8")
        self.assertEqual(read(d)[2][0]["code"], "unparsed_records")

    def test_extract_skips_code_fences(self):
        t = Transcript()
        t.prompt()
        t.say("Example:\n```\nThe tests pass.\n```")
        t.doc()
        self.assertEqual(extract(read(self._p(t))[1]), [])

    def _p(self, t):
        p = Path(tempfile.mkdtemp()) / "t.jsonl"
        p.write_text("\n".join(t.lines), encoding="utf-8")
        return p

    def _spawn(self):
        """Parent spawns one subagent that edits a.py; returns (parent, subs)."""
        t = Transcript()
        t.prompt()
        tid = f"t{t.n}"
        t.run("Agent", {"description": "fix", "prompt": "fix a.py"}, "done")
        s = Transcript()
        s.run("Edit", {"file_path": "C:\\repo\\a.py"}, "The file C:\\repo\\a.py has been updated successfully.")
        s.say("Fixed the bug in a.py.")
        return t, {("abc123", tid): s}

    def test_subagent_present(self):
        t, subs = self._spawn()
        doc = t.doc(subs=subs)
        self.assertEqual({e.get("agent") for e in doc["events"]}, {None, "abc123"})
        codes = [n["code"] for n in doc["notes"]]
        self.assertIn("subagents_folded", codes)
        self.assertNotIn("subagent_not_visible", codes)
        sub_tools = [e["tool"] for e in doc["events"] if e.get("agent") and e["kind"] == "tool"]
        self.assertEqual(sub_tools, ["Edit"])
        self.assertEqual([(c["type"], c.get("agent"), c["status"]) for c in doc["claims"]],
                         [("file_change", "abc123", "observed")])

    def test_subagent_missing(self):
        t, _ = self._spawn()
        doc = t.doc()
        codes = [n["code"] for n in doc["notes"]]
        self.assertIn("subagent_not_visible", codes)
        self.assertNotIn("subagents_folded", codes)
        self.assertTrue(all("agent" not in e for e in doc["events"]))

    def test_parent_claims_subagent_edit(self):
        t, subs = self._spawn()
        t.say("I updated a.py.")
        doc = t.doc(subs=subs)
        mine = [c for c in doc["claims"] if "agent" not in c]
        self.assertEqual([(c["type"], c["status"]) for c in mine], [("file_change", "observed")])
        edit = [e["seq"] for e in doc["events"] if e.get("agent") and e["tool"] == "Edit"]
        self.assertEqual(mine[0]["evidence"], edit)
        self.assertEqual(len(doc["claims"]), 2)  # the subagent's own claim stays separate

    def test_helpers(self):
        self.assertEqual(scrub("PGPASSWORD=hunter2 ok"), "[REDACTED] ok")
        for leaky in ('{"password": "FAKE-x y"}', "Authorization: Basic FAKE-abc123", "--password FAKE-pw",
                      "curl -u FAKE-user:FAKE-pw host", "api_key = 'FAKE one two'", "TOKEN=FAKE-t"):
            self.assertNotIn("FAKE", scrub(leaky), leaky)
        for clean in ("git push -u origin main", "run the tests, then stop", "secretary: Bob"):
            self.assertEqual(scrub(clean), clean)
        self.assertEqual(norm_path("/c/Data/x/"), "c:/data/x")
        self.assertEqual(fmt_et("2026-10-29T17:59:11Z"), "13:59:11")  # EDT
        self.assertEqual(fmt_et("2026-12-01T17:00:00Z"), "12:00:00")  # EST


if __name__ == "__main__":
    unittest.main()
