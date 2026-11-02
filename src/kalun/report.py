"""Build the kalun.json document for one transcript."""
import json
from pathlib import Path

from . import SCHEMA_VERSION, __version__
from .claims import EDIT_TOOLS, check, extract, git_repos, overall
from .gitinfo import Git, session_git
from .reader import SHELL, read, sha256_file
from .util import norm_path, scrub, under

FINAL_MESSAGE_CHARS = 600


def build(transcript, out_dir, git=None, refresh=False):
    git = git or Git()
    meta, events, notes = read(transcript)
    git_block, changed = session_git(git, out_dir, meta["cwd_start"], meta["started_at"], refresh)

    claims = check(events, extract(events), git_block, changed, git)
    for c in claims:
        c["text"] = scrub(c["text"])

    root = git_block["repo_root"]
    for e in events:
        if e["tool"] in SHELL:
            for repo, _ in git_repos(e):
                shown = norm_path(repo, lower=False)
                if (not root or not under(repo, root)) and shown not in git_block["other_repos_touched"]:
                    git_block["other_repos_touched"].append(shown)

    texts = [e for e in events if e["kind"] == "assistant_text"]
    if texts:
        texts[-1]["summary"] = scrub(texts[-1]["_x"]["text"].strip())[:FINAL_MESSAGE_CHARS]

    denied = sum(1 for e in events if e["outcome"] == "denied")
    if denied:
        notes.append(dict(code="denied", detail=f"{denied} tool call(s) were denied and are not counted as evidence"))
    if not git_block["available"]:
        notes.append(dict(code="git_unavailable", detail="start directory is not in a git repo"))
    elif git_block["head_before_source"] == "derived":
        notes.append(dict(code="head_before_derived", detail="no SessionStart snapshot; HEAD before was derived from the start time"))
    for r in git_block["other_repos_touched"]:
        notes.append(dict(code="other_repo_touched", detail=r))
    if not claims:
        notes.append(dict(code="no_claims", detail="no checkable claims found in assistant text"))

    meta.update(transcript_path=str(transcript), transcript_sha256=sha256_file(transcript))
    keys = ["id", "transcript_path", "transcript_sha256", "claude_code_version", "cwd_start", "git_branch",
            "started_at", "ended_at", "turns", "compactions", "continued_in"]
    return dict(
        schema_version=SCHEMA_VERSION, kalun_version=__version__,
        session={k: meta[k] for k in keys},
        git=git_block,
        events=[{k: v for k, v in e.items() if k != "_x"} for e in events],
        claims=[{k: c[k] for k in ("id", "type", "subject", "text", "event_seq", "status", "reason", "evidence", "agent")
                 if k in c}
                for c in claims],
        overall_status=overall(claims),
        notes=notes,
    )


def dump(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_all(doc, out_dir):
    from .render import flight_log, receipt
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "kalun.json").write_text(dump(doc), encoding="utf-8", newline="\n")
    (out / "flight-log.md").write_text(flight_log(doc), encoding="utf-8", newline="\n")
    (out / "receipt.md").write_text(receipt(doc), encoding="utf-8", newline="\n")
    return out
