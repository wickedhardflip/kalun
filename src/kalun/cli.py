"""kalun report [session-id] | kalun hook start|stop"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__
from .gitinfo import Git
from .report import build, write_all


def kalun_home():
    return Path(os.environ.get("KALUN_HOME") or Path.home() / ".kalun")


def projects_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def encode_cwd(cwd):
    """Claude Code names a project folder after its cwd, every character outside [A-Za-z0-9] turned into '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))  # simplified: Claude Code also shortens names over 200 chars with a hash; those need --transcript


def find_transcript(session_id, cwd):
    root = projects_dir()
    if session_id:
        return next(iter(sorted(root.glob(f"*/{session_id}*.jsonl"))), None)
    folder = root / encode_cwd(cwd)
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return files[-1] if files else None


def cmd_report(args):
    path = Path(args.transcript) if args.transcript else find_transcript(args.session, Path.cwd())
    if not path or not path.is_file():
        print("kalun: no transcript found. Pass a session id or --transcript PATH.", file=sys.stderr)
        return 2
    sid = path.stem
    out = Path(args.out) if args.out else kalun_home() / sid
    doc = build(path, out, Git(), refresh=args.refresh)
    write_all(doc, out)
    print(f"Overall: {doc['overall_status'].upper()}  ({len(doc['claims'])} claims)")
    print(out)
    return 0


def cmd_hook(args):
    """SessionStart / Stop hook: write a git snapshot for the session. Reads the hook JSON from stdin, never blocks."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
        sid, cwd = data["session_id"], data.get("cwd") or os.getcwd()
        out = kalun_home() / sid
        snap = Git().snapshot(cwd)
        if snap:
            out.mkdir(parents=True, exist_ok=True)
            name = "git-before.json" if args.when == "start" else "git-after.json"
            if args.when == "start" and (out / name).exists():
                return 0  # a resumed session keeps its first snapshot
            (out / name).write_text(json.dumps(snap, indent=2), encoding="utf-8")
    except Exception as exc:  # a hook must never get in the way of the session
        print(f"kalun hook: {exc}", file=sys.stderr)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="kalun", description="A flight log and receipt for Claude Code sessions.")
    p.add_argument("--version", action="version", version=f"kalun {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="write kalun.json, flight-log.md and receipt.md for a session")
    r.add_argument("session", nargs="?", help="session id (default: latest for this directory)")
    r.add_argument("--transcript", help="path to a transcript .jsonl instead of looking one up")
    r.add_argument("--out", help="output folder (default: ~/.kalun/<session-id>)")
    r.add_argument("--refresh", action="store_true", help="take a fresh git-after snapshot")
    r.set_defaults(fn=cmd_report)
    h = sub.add_parser("hook", help="git snapshot for a SessionStart or Stop hook")
    h.add_argument("when", choices=["start", "stop"])
    h.set_defaults(fn=cmd_hook)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
