# Kalun

Local-first, observation-only flight log and receipt for Claude Code sessions. Reads transcripts after the fact; changes nothing.

## Scope limits (do not widen without Brian's OK)
Claude Code only. Local files only. No steering, no dashboard or web UI, no other agents, no policy engine, no accounts or telemetry, no LLM calls, no live observer. No dependencies (stdlib only, Python 3.9+).

## Publishing rule (standing)
Only the project goes to GitHub, never Brian's results or session data. Fixtures are synthetic (built with `Transcript` in `tests/test_rules.py`), never trimmed real transcripts. Never commit anything from `~/.kalun/`, `~/.claude/` or a real session. `tests/test_hygiene.py` enforces this; run the tests before every push, and add to its FORBIDDEN list when a new kind of leak is possible.

## Layout
- `src/kalun/`: `reader` (transcript to events), `claims` (extract and check claims), `gitinfo` (read-only git), `report` (assemble), `render` (Markdown), `cli`
- `fixtures/<name>/`: synthetic transcripts plus golden outputs
- `tests/`: stdlib `unittest`

## Rules
- JSON is the source of truth; Markdown is rendered from it. Output must be deterministic (no generation timestamps).
- Git access is read-only (see the allowlist in `gitinfo.Git.run`).
- Scrub secrets on all output.
- Run tests: `python -m unittest discover -s tests`. Refresh goldens only for a deliberate change: `KALUN_UPDATE=1 ...`, then read the diff.
- Open work lives in `TASKS.md`.
