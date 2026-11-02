"""Read a Claude Code JSONL transcript into ordered events.

File order is the only order. Timestamps are copied, never sorted on.
Each event carries a private `_x` dict (raw input, result text, cwd, turn)
that the claim checks use and the JSON writer drops.
"""
import hashlib
import json
import re
from pathlib import Path

from .util import scrub

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CWD_RESET = re.compile(r"\n?Shell cwd was reset to .*\s*$")

FAIL = [
    re.compile(p, re.M)
    for p in (
        r"^FAILED ",
        r"^ERROR ",
        r"\b\d+ (failed|errors?)\b",
        r"^E {2,}",
        r"Traceback \(most recent call last\)",
        r"AssertionError",
        r"^--- FAIL",
        r"^FAIL\s",
        r"npm ERR!",
        r"error (TS|CS)\d+",
        r"Build FAILED",
        r"Failed to compile",
        r"panic:",
    )
]
OK = [
    re.compile(p, re.M)
    for p in (
        r"\b\d+ passed\b",
        r"^Tests:.*\d+ passed",
        r"Passed!",
        r"^Ran \d+ tests? in [\d.]+s\s+OK\b",
        r"Build succeeded",
        r"^ok\s",
        r"test result: ok",
        r"built in \d",
        r"Compiled successfully",
        r"0 Error\(s\)",
    )
]
PROGRESS = re.compile(r"^[.sxXFE]+\s+\[\s*\d+%\]$")
PROGRESS_DONE = re.compile(r"^[.sx]+\s*\[100%\]$")

SHELL = ("Bash", "PowerShell")
MASK = re.compile(r"\|\s*(tail|head|grep|sed|awk|tee|wc)\b|\|\|\s*(true|:|echo)|2>&1\s*\|")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\s*\1\b", re.S)
SPLIT = re.compile(r"&&|\|\||;|\||\n")
IGNORED = re.compile(r"^\s*(grep|rg|echo|cat|sed\s+-n|ls|head|tail|git\s+(log|diff|show))\b")


def segments(cmd):
    """Command split into segments, heredoc bodies dropped, read-only ones ignored."""
    cmd = HEREDOC.sub("", cmd)
    return [s.strip() for s in SPLIT.split(cmd) if s.strip() and not IGNORED.match(s)]


def is_masked(cmd):
    return bool(MASK.search(cmd))


def result_text(content):
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    text = ANSI.sub("", str(content or "")).replace("\r\n", "\n")
    return CWD_RESET.sub("", text)


def progress_only_ok(text):
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    prog = [ln for ln in lines if PROGRESS.match(ln)]
    return bool(prog) and bool(PROGRESS_DONE.match(lines[-1])) and not any(
        re.search(r"[FE]", ln.split("[")[0]) for ln in prog
    )


def classify(text, is_error, interrupted, markers=True):
    """First match wins: denied, interrupted, exit code, is_error, failure marker, success marker.

    Only shell output is scanned for markers; an Edit result echoes file lines that could
    contain any of those words. Other tools are ok unless they report an error.
    """
    if text.startswith("Permission for this action was denied"):
        return "denied"
    if interrupted or "[Request interrupted" in text:
        return "interrupted"
    m = re.match(r"Exit code (\d+)", text)
    if m and int(m.group(1)) != 0:
        return "failed"
    if is_error:
        return "failed"
    if not markers:
        return "ok"
    if any(rx.search(text) for rx in FAIL):
        return "failed"
    if any(rx.search(text) for rx in OK) or progress_only_ok(text):
        return "ok"
    return "unknown"


def summarize(name, inp):
    if name in ("Bash", "PowerShell"):
        s = (inp.get("command") or "").strip().split("\n")[0]
    elif name in ("Edit", "Write", "NotebookEdit", "Read", "MultiEdit"):
        s = inp.get("file_path") or inp.get("notebook_path") or ""
    elif name == "Agent":
        s = inp.get("description") or ""
    else:
        s = next((str(v) for v in inp.values() if isinstance(v, (str, int))), "")
    return scrub(f"{name} {s}".strip())[:200]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def real_prompt(rec):
    c = rec.get("message", {}).get("content")
    return c if isinstance(c, str) and not rec.get("isMeta") and not c.lstrip().startswith("<") else None


def read(path):
    """Return (session_meta, events, notes). Streams the file once."""
    events, notes = [], []
    meta = dict(
        id=None, claude_code_version=None, cwd_start=None, git_branch=None,
        started_at=None, ended_at=None, turns=0, compactions=0, continued_in=None,
    )
    bad = 0
    turn = 0
    seen = set()
    pending = {}  # tool_use_id -> event
    agent_calls = 0
    cur_agent = None

    def add(kind, ts, summary, tool=None, cwd=None, **x):
        ev = dict(seq=len(events) + 1, ts=ts, kind=kind, tool=tool, dir=cwd,
                  summary=summary, outcome=None, uuid=x.pop("uuid", None))
        ev["_x"] = dict(turn=turn, cwd=cwd, **x)
        if cur_agent:
            ev["agent"] = cur_agent
        events.append(ev)
        return ev

    def scan(fp, agent=None):
        nonlocal bad, turn, agent_calls, cur_agent
        cur_agent = agent
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if not isinstance(rec, dict):
                    bad += 1
                    continue
                t, ts, cwd = rec.get("type"), rec.get("timestamp"), rec.get("cwd")
                if t == "continued-in":
                    meta["continued_in"] = rec.get("continuedInSessionId")
                    continue
                if t == "system" and rec.get("subtype") == "compact_boundary":
                    meta["compactions"] += 1
                    add("boundary", ts, "Conversation compacted", uuid=rec.get("uuid"))
                    continue
                if t not in ("user", "assistant"):
                    continue
                if meta["id"] is None and not agent:
                    meta.update(id=rec.get("sessionId"), cwd_start=cwd, git_branch=rec.get("gitBranch"),
                                claude_code_version=rec.get("version"), started_at=ts)
                if not agent:
                    meta["ended_at"] = ts or meta["ended_at"]
                msg = rec.get("message") or {}
                content = msg.get("content")
                if t == "user":
                    p = None if agent else real_prompt(rec)  # a subagent's task text is in the parent's Agent call
                    if p is not None:
                        turn += 1
                        add("prompt", ts, scrub(p.strip().replace("\n", " "))[:200], cwd=cwd,
                            uuid=rec.get("uuid"), text=p)
                    elif isinstance(content, list):
                        for b in content:
                            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                                continue
                            ev = pending.get(b.get("tool_use_id"))
                            if not ev:
                                continue
                            text = result_text(b.get("content"))
                            tur = rec.get("toolUseResult")
                            intr = bool(tur.get("interrupted")) if isinstance(tur, dict) else False
                            ev["outcome"] = classify(text, bool(b.get("is_error")), intr,
                                                     markers=ev["tool"] in SHELL)
                            ev["_x"]["result"] = text
                    continue
                if not isinstance(content, list):
                    continue
                mid = msg.get("id")
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        key = (mid, "t", b["text"])
                        if key in seen:
                            continue
                        seen.add(key)
                        add("assistant_text", ts, scrub(b["text"].strip().replace("\n", " "))[:200],
                            cwd=cwd, uuid=rec.get("uuid"), text=b["text"], msg_id=mid,
                            stop=msg.get("stop_reason"))
                    elif b.get("type") == "tool_use":
                        if b.get("id") in seen:
                            continue
                        seen.add(b.get("id"))
                        inp = b.get("input") or {}
                        name = b.get("name")
                        agent_calls += name == "Agent"
                        ev = add("tool", ts, summarize(name, inp), tool=name, cwd=cwd,
                                 uuid=rec.get("uuid"), input=inp)
                        ev["outcome"] = "unknown"
                        pending[b.get("id")] = ev
    scan(path)
    top_turn = turn
    sub_dir = Path(path).with_suffix("") / "subagents"
    placed = {}  # spawning event seq -> subagent events already placed after it
    found = 0
    for fp in sorted(sub_dir.glob("agent-*.jsonl")):  # simplified: one directory level, no nested-agent tree
        try:
            spawn = pending.get(json.loads(fp.with_suffix(".meta.json").read_text(encoding="utf-8")).get("toolUseId"))
        except (OSError, ValueError, AttributeError):
            spawn = None
        turn = spawn["_x"]["turn"] if spawn else top_turn
        start = len(events)
        scan(fp, fp.stem[len("agent-"):])
        found += 1
        chunk = events[start:]
        del events[start:]
        at = events.index(spawn) + 1 + placed.get(spawn["seq"], 0) if spawn else len(events)
        placed[spawn["seq"] if spawn else None] = placed.get(spawn["seq"] if spawn else None, 0) + len(chunk)
        events[at:at] = chunk
    turn = top_turn
    for i, e in enumerate(events, 1):
        e["seq"] = i
    meta["turns"] = turn
    if bad:
        notes.append(dict(code="unparsed_records", detail=f"{bad} line(s) could not be parsed"))
    if meta["compactions"]:
        notes.append(dict(code="compaction", detail=f"{meta['compactions']} compaction(s); claims judged against all evidence in the file"))
    if found:
        notes.append(dict(code="subagents_folded", detail=f"{found} subagent transcript(s) folded into this session"))
    if agent_calls > found:
        notes.append(dict(code="subagent_not_visible", detail=f"{agent_calls - found} subagent transcript(s) not found"))
    return meta, events, notes
