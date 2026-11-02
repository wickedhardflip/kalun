"""Render flight-log.md and receipt.md from a kalun.json document. Times are shown in Eastern."""
from .util import fmt_et, scrub

LIMITS = (
    "Kalun reads the transcript Claude Code wrote, plus git state before and after. "
    "It cannot see subagents whose transcripts are missing, edits made by shell commands, or anything that never reached the transcript."
)


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _git_line(g):
    if not g["available"]:
        return "not available"
    before = (g["head_before"] or "?")[:7]
    after = (g["head_after"] or "?")[:7]
    src = g["head_before_source"]
    where = f", branch {g['branch']}" if g.get("branch") else ""
    if g.get("linked_worktree"):
        where += f", linked worktree of {g['common_dir']}"
    return f"{g['repo_root']}{where}, HEAD {before} to {after} (before: {src})"


def flight_log(doc):
    s, g = doc["session"], doc["git"]
    notes = ", ".join(n["code"] for n in doc["notes"]) or "none"
    out = [
        "# Kalun Flight Log",
        "",
        f"- Session: {(s['id'] or '')[:8]}",
        f"- Claude Code: {s['claude_code_version'] or 'unknown'}",
        f"- Started: {fmt_et(s['started_at'], True) if s['started_at'] else 'unknown'}",
        f"- Ended: {fmt_et(s['ended_at'], True) if s['ended_at'] else 'unknown'}",
        f"- Directory: {s['cwd_start']}",
        f"- Branch: {s['git_branch'] or 'unknown'}",
        f"- Git: {_git_line(g)}",
        f"- Transcript: {s['transcript_path']} (sha256 {s['transcript_sha256']})",
        f"- Notes: {notes}",
    ]
    turn = None
    n = 0
    for e in doc["events"]:
        if e["kind"] == "prompt":
            n += 1
            turn = n
            out += ["", f'## Turn {n}: "{e["summary"][:80]}"', ""]
        elif turn is None:
            turn = 0
            out += ["", "## Before the first prompt", ""]
        if e["kind"] == "tool":
            label, rest = e["tool"], e["summary"][len(e["tool"]) + 1:]
            label += f" {e['outcome']}"
        else:
            label, rest = {"prompt": "prompt", "assistant_text": "assistant", "boundary": "boundary"}[e["kind"]], e["summary"]
        if e.get("agent"):
            label = f"[{e['agent'][:8]}] {label}"
        out.append(f"- {fmt_et(e['ts']) if e['ts'] else '--:--:--'}  #{e['seq']}  {label} {rest}".rstrip())
    return "\n".join(out) + "\n"


def receipt(doc):
    s, g = doc["session"], doc["git"]
    ev = doc["events"]
    shell = [e for e in ev if e["tool"] in ("Bash", "PowerShell")]
    wrote = sorted({e["summary"].split(" ", 1)[1] for e in ev
                    if e["tool"] in ("Edit", "Write", "NotebookEdit", "MultiEdit") and e["outcome"] == "ok" and " " in e["summary"]})
    final = next((e["summary"] for e in reversed(ev) if e["kind"] == "assistant_text" and not e.get("agent")), "")
    out = [
        "# Kalun Receipt",
        "",
        f"Overall: {doc['overall_status'].upper()}",
        "",
        f"Session {(s['id'] or '')[:8]}, {fmt_et(s['started_at'], True) if s['started_at'] else 'unknown'} to "
        f"{fmt_et(s['ended_at'], True) if s['ended_at'] else 'unknown'}, {s['turns']} turns.",
        "",
    ]
    if doc["claims"]:
        out += ["| Claim | Status | Why | Evidence |", "|---|---|---|---|"]
        for c in doc["claims"]:
            ev_ref = ", ".join(f"#{i}" for i in c["evidence"]) or "none"
            out.append(f"| {_cell(('[' + c['agent'][:8] + '] ' if c.get('agent') else '') + c['text'][:80])} | {c['status']} | {c['reason'].replace('_', ' ')} | {ev_ref} |")
    else:
        out.append("No checkable claims were found in the assistant's text.")
    out += ["", "## Final message (verbatim, scrubbed)", ""]
    out += [f"> {scrub(ln)}" for ln in final.split("\n")] if final else ["> (none)"]
    out += [
        "", "## Observed", "",
        f"- Commands run: {len(shell)} ({sum(e['outcome'] == 'failed' for e in shell)} failed, "
        f"{sum(e['outcome'] == 'denied' for e in shell)} denied)",
        f"- Files written: {', '.join(wrote[:10]) + (f' and {len(wrote) - 10} more' if len(wrote) > 10 else '') if wrote else 'none'}",
        f"- Git: {_git_line(g)}",
    ]
    if g["other_repos_touched"]:
        out.append(f"- Other repos touched: {', '.join(g['other_repos_touched'])}")
    out += ["", "## Limits", "", LIMITS]
    return "\n".join(out) + "\n"
