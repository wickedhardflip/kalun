# Kalun

**A flight log and a receipt for your Claude Code sessions.**

Claude Code tells you what it did. Kalun checks.

After a session, Kalun reads the transcript Claude Code already keeps on your machine and writes two plain Markdown files: a flight log of what happened, and a receipt that sets every "tests pass", "committed" or "file updated" in the final message against the evidence. Each claim ends up observed, partial or unverified, with the reason and the step it points to.

It watches. It never steers. Nothing leaves your machine, nothing in your project is changed, and there is no server, account, model call or dependency.

## Why

A session ends with a confident summary. Sometimes the tests were never run, the last run failed, the files changed after the run, or the commit never landed. Reading a long transcript to find out is slow, so most people don't. Kalun does that reading and hands you a one page receipt.

## Quick start

```
git clone https://github.com/wickedhardflip/kalun
cd kalun
pip install -e .

kalun report              # latest session for the current directory
kalun report <session-id> # a specific session
```

Or install straight from GitHub: `pip install git+https://github.com/wickedhardflip/kalun`

Python 3.9 or newer. Standard library only.

Output lands in `~/.kalun/<session-id>/`:

| File | What it is |
|---|---|
| `kalun.json` | The source of truth. Everything else is rendered from it. |
| `flight-log.md` | A turn by turn list of what happened, in Eastern time. |
| `receipt.md` | Each claim with its status, reason and evidence, plus the final message. |

Run the report twice on the same session and you get identical files.

## What a receipt looks like

```
# Kalun Receipt

Overall: PARTIAL

| Claim                                              | Status   | Why                           | Evidence |
|----------------------------------------------------|----------|-------------------------------|----------|
| I updated README.md and committed it as 4bb0767.   | partial  | commit ran head not checkable | #3 |
| I updated README.md and committed it as 4bb0767.   | observed | tool confirmed only           | #2 |
```

The overall status is the worst of the claims. A session with no claims is `unverified` and says so, because silence is not proof.

## Statuses

- **observed**: the transcript, or git, shows it happened.
- **partial**: something supports it, but not all of it. A test run came before later edits, or a commit command ran and the new HEAD could not be checked.
- **unverified**: nothing supports it, or the evidence points the other way, such as a failed test run or a denied command.

## Claim types

| Type | Example sentence | How it is checked |
|---|---|---|
| `tests_pass` | "All tests pass." | A test command ran before the claim, did not fail, printed a success marker, and no files changed after it. |
| `file_change` | "I updated README.md." | A successful edit or write tool call touched that path. |
| `committed` | "Committed as 4bb0767." | A commit command ran, HEAD moved, and the commit exists in the repo (read only check). |
| `build_ok` | "The build succeeds." | A build command ran and printed a success marker. |
| `other` | "Deployed to staging." | Never verified. Kalun can only see your machine. |

Hedged sentences ("I'll commit once the tests pass", "it isn't pushed yet") are skipped on purpose. Sentences with an exception ("all tests pass except the slow one") are capped at partial.

## Git hooks (optional, recommended)

Hooks let Kalun record repo state at the start and end of a session. They only write small files under `~/.kalun/` and never block a session.

Merge the entries from [`hooks/settings.example.json`](hooks/settings.example.json) into your Claude Code `settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "kalun hook start" }] }],
    "Stop":         [{ "hooks": [{ "type": "command", "command": "kalun hook stop" }] }]
  }
}
```

Without hooks Kalun still works. It works out the starting commit from the session start time and marks it `derived`.

## Limits

Kalun is deliberately small. Know what it cannot do:

- It reads the transcript, so it is only as complete as the transcript.
- Work done by subagents is not visible, only the call that started them.
- Files changed by shell commands are not tracked, only changes made with the edit tools.
- Claim detection uses plain patterns. It can miss a claim or pick up a loose one.
- Claude Code only. Local files only. It does not steer a session, run a server, or call a model.

## Privacy

Everything stays on your machine. Output is scrubbed for common secret patterns, and transcripts are only read, never copied elsewhere. Kalun makes no network calls.

## Development

```
python -m unittest discover -s tests
```

Golden tests run against made-up transcripts in [`fixtures/`](fixtures/README.md). After a deliberate output change, refresh them with `KALUN_UPDATE=1 python -m unittest discover -s tests` and read the diff.

## License

MIT
