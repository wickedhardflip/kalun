# Kalun tasks

- [ ] (in progress: worktrees + subagents, 2026-11-02) **START HERE (Brian's ask):** make Kalun work in git worktrees and with subagents, since many people use both. Findings so far: subagent transcripts exist at `~/.claude/projects/<proj>/<session>/subagents/agent-<id>.jsonl` (+ `.meta.json`), so reader.py can fold them in instead of only noting `subagent_not_visible`. Worktree sessions have the worktree as cwd, so transcript lookup (`encode_cwd`) and git snapshots should follow it, but this is untested; also check hooks started in one worktree and report run from another, `git rev-parse --git-common-dir` for the shared repo, and subagents running in their own worktree. Needs fixtures for both, own worktree + branch, Brian's OK before code changes.
- [ ] Use it live for a few sessions; collect false or missed claims and tighten the patterns
- [ ] Decide whether to make the repo public
- [ ] Claims whose subject is a thing, not a path ("added a purple theme", "Fixed the Spinner bug") are not extracted; strict design cannot check them
- [ ] Files changed by shell/PowerShell (e.g. settings.json edits) are not tracked; documented limit, hooks give git evidence only
- [ ] Check on a second real session that the reader's user/assistant-only filter is not dropping useful records
