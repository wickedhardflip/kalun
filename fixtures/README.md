# Fixtures

Each folder holds a small made-up Claude Code transcript and the output Kalun produces for it. They are synthetic, so they contain no real session data.

- `transcript.jsonl` is the input.
- `expected.json`, `flight-log.md` and `receipt.md` are the committed outputs. The tests run Kalun with git unavailable, so results do not depend on any repo on your machine.

To refresh the outputs after a deliberate change, run `KALUN_UPDATE=1 python -m unittest discover -s tests` and read the diff before committing.
