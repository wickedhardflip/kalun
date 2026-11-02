"""Read-only git helpers and the before/after snapshots.

Every git call goes through Git.run, which only ever runs read-only subcommands.
Tests swap in a stub for Git.run.
"""
import json
import subprocess
from pathlib import Path

from .util import norm_path

READ_ONLY = {"rev-parse", "rev-list", "status", "diff", "cat-file", "log"}


class Git:
    def run(self, repo, *args):
        """stdout of `git -C repo args`, or None on any failure. Read-only subcommands only."""
        if args[0] not in READ_ONLY:
            raise ValueError(f"kalun only runs read-only git commands, not {args[0]}")
        try:
            p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                               text=True, timeout=20, encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout if p.returncode == 0 else None

    def root(self, path):
        out = self.run(path, "rev-parse", "--show-toplevel")
        return norm_path(out.strip(), lower=False) if out and out.strip() else None

    def head(self, repo):
        out = self.run(repo, "rev-parse", "HEAD")
        return out.strip() if out and out.strip() else None

    def status(self, repo):
        out = self.run(repo, "status", "--porcelain", "--untracked-files=all")
        if out is None:
            return None
        # porcelain v1: "XY path" (renames: "XY old -> new")
        return sorted(ln[3:].split(" -> ")[-1].strip('"') for ln in out.splitlines() if len(ln) > 3)

    def identity(self, root):
        """Which repo and checkout this is. In a linked worktree `.git` is a file and --git-dir differs
        from --git-common-dir (the shared repo). Empty values when git cannot say."""
        out = self.run(root, "rev-parse", "--git-common-dir", "--git-dir")
        lines = out.splitlines() if out else []
        branch = (self.run(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
        if len(lines) != 2:
            return dict(common_dir=None, linked_worktree=None, branch=branch)
        common, gdir = (Path(root, ln).resolve() for ln in lines)  # relative output is relative to root
        return dict(common_dir=norm_path(str(common), lower=False), linked_worktree=common != gdir, branch=branch)

    def snapshot(self, path):
        root = self.root(path)
        if not root:
            return None
        return dict(repo_root=root, head=self.head(root), status=self.status(root), **self.identity(root))

    def head_at(self, repo, ts):
        out = self.run(repo, "rev-list", "-1", f"--before={ts}", "HEAD")
        return out.strip() or None if out else None

    def commits_between(self, repo, a, b):
        out = self.run(repo, "rev-list", f"{a}..{b}")
        return out.split() if out else []

    def changed_between(self, repo, a, b):
        out = self.run(repo, "diff", "--name-only", f"{a}..{b}")
        return [ln for ln in out.splitlines() if ln] if out else []

    def commit_exists(self, repo, sha):
        return self.run(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def session_git(git, out_dir, cwd_start, started_at, refresh=False):
    """Return the `git` block of kalun.json plus the repo-level data the claim checks need.

    Before: the SessionStart hook's git-before.json if present, else HEAD derived from the
    start time. After: git-after.json if present (the Stop hook, or an earlier report run),
    else a snapshot taken now and saved, unless refresh is set.
    """
    out_dir = Path(out_dir)
    block = dict(available=False, repo_root=None, common_dir=None, linked_worktree=None, branch=None, head_before=None, head_before_source=None,
                 head_after=None, status_before=None, status_after=None, other_repos_touched=[])
    before = _load(out_dir / "git-before.json")
    root = before["repo_root"] if before else (git.root(cwd_start) if cwd_start else None)
    if not root:
        return block, []
    block.update(available=True, repo_root=root)
    block.update(git.identity(root) if not before or "common_dir" not in before else
                 {k: before[k] for k in ("common_dir", "linked_worktree", "branch")})
    if before:
        block.update(head_before=before.get("head"), head_before_source="hook",
                     status_before=before.get("status"))
    else:
        block.update(head_before=git.head_at(root, started_at), head_before_source="derived")
    after = None if refresh else _load(out_dir / "git-after.json")
    if after is None:
        after = git.snapshot(root)
        if after:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "git-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
    if after:
        block.update(head_after=after.get("head"), status_after=after.get("status"))
    changed = []
    if block["head_before"] and block["head_after"] and block["head_before"] != block["head_after"]:
        changed = git.changed_between(root, block["head_before"], block["head_after"])
    return block, changed
