"""Find what the assistant said it did, then check each statement against the transcript and git."""
import posixpath
import re

from .reader import SHELL, is_masked, segments
from .util import is_abs, norm_path, under

RANK = {"observed": 2, "partial": 1, "unverified": 0}

# ---- extraction -----------------------------------------------------------

FENCE = re.compile(r"```.*?```", re.S)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
HEDGE = re.compile(r"\b(will|going to|need to|should|would|if|once|until|not|no|unable|might|may|could|probably|likely)\b|n't|'ll", re.I)
CAP = re.compile(r"\b(except|but|aside from)\b", re.I)

TESTS = re.compile(r"\btests?\b.{0,40}\b(pass(es|ed|ing)?|green)\b|\ball (tests )?(pass|green)"
                   r"|\b(suite|pytest|unittest|jest|vitest)\b.{0,30}\b(pass(es|ed|ing)?|green)\b", re.I)
VERB = (r"(created|wrote|added|updated|edited|modified|fixed|renamed|deleted|changed|replaced|rewrote|"
        r"implemented|built|appended|patched|saved|generated|refactored|written|rewritten|removed)")
PATH = re.compile(r"(?<![\w./\\-])((?:[A-Za-z]:)?[\w./\\-]*\w\.[A-Za-z]\w{0,5})(?![\w/\\-])")
# "README.md was updated", "config.json has been rewritten": the path comes before the verb
PASSIVE = re.compile(PATH.pattern + r"\s+(?:is|are|was|were|has been|have been|got)(?: now)?\s+" + VERB + r"\b", re.I)
NOT_PATHS = {"e.g", "i.e"}
COMMIT = re.compile(r"\bcommitted\b(?!\s+(?:files?|changes?|code|work|state|version)\b)|\bmade a commit\b"
                    r"|\bcreated a commit\b|\bcommit (?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", re.I)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
BUILD = re.compile(r"\bbuild(s|ing)?\b.{0,30}\b(succeed\w*|pass\w*|clean|ok|green|complete\w*)\b|\bcompiles cleanly\b"
                   r"|\bbuilt (successfully|cleanly)\b|\b(tsc|typecheck|type-check|compiles?|compiled)\b.{0,20}\b(pass\w*|clean\w*|succe\w+|ok)\b", re.I)
OTHER = re.compile(r"\b(pushed|deployed|merged|published|released)\b", re.I)


def extract(events):
    """Candidate claims from assistant text, one per (turn, type, subject), last wins."""
    found = {}
    for ev in events:
        if ev["kind"] != "assistant_text":
            continue
        text = FENCE.sub(" ", ev["_x"]["text"]).replace("`", "")
        for sent in SENTENCE.split(text):
            sent = re.sub(r"^(?:[\s\-*•>#]+|\d+[.)]\s+)", "", sent.replace("**", "")).strip()
            if not sent:
                continue
            for typ, subj, m in _matches(sent):
                if HEDGE.search(sent[: m.end()]):
                    continue
                key = (ev["_x"]["turn"], ev.get("agent"), typ, subj)
                found.pop(key, None)  # re-insert so order follows the last mention
                found[key] = dict(
                    type=typ, subject=subj, text=sent[:200], event_seq=ev["seq"],
                    cap=bool(CAP.search(sent)))
                if ev.get("agent"):
                    found[key]["agent"] = ev["agent"]
    out = sorted(found.values(), key=lambda c: c["event_seq"])
    for i, c in enumerate(out, 1):
        c["id"] = f"c{i}"
    return out


def _matches(sent):
    for m in TESTS.finditer(sent):
        yield "tests_pass", None, m
    for m in BUILD.finditer(sent):
        yield "build_ok", None, m
    for m in COMMIT.finditer(sent):
        sha = SHA.search(sent)
        yield "committed", sha.group(0) if sha else None, m
    for m in OTHER.finditer(sent):
        yield "other", m.group(1).lower(), m
    for v in re.finditer(r"\b" + VERB + r"\b", sent, re.I):
        for p in PATH.finditer(sent, v.end()):
            if p.group(1).lower() not in NOT_PATHS:
                yield "file_change", p.group(1).replace("\\", "/"), p
    for m in PASSIVE.finditer(sent):
        if m.group(1).lower() not in NOT_PATHS:
            yield "file_change", m.group(1).replace("\\", "/"), m


# ---- evidence -------------------------------------------------------------

TEST_CMD = re.compile(r"(?:\b(?:pytest|py\.test|npm (?:run )?test|yarn test|pnpm test|dotnet test|go test|cargo test|vitest|jest)|-m (?:pytest|unittest))\b")
BUILD_CMD = re.compile(r"\b(npm run build|npm run typecheck|yarn build|pnpm build|dotnet (build|publish)|cargo (build|check)|go build|vite build|next build|tsc|msbuild|make|docker build)\b|\bpy_compile\b")
COMMIT_CMD = re.compile(r"\bgit\b(\s+-C\s+\S+)?.*\bcommit\b")
SED_I = re.compile(r"\bsed\s+(-[a-zA-Z]*i|--in-place)")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
SHA_OUT = [re.compile(r"^\[[^\]]+ ([0-9a-f]{7,40})\]", re.M), re.compile(r"^([0-9a-f]{7,40})\s", re.M)]
CD = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|\S+)""")
GIT_C = re.compile(r"""\bgit\s+(?:-c\s+\S+\s+)*-C\s+("[^"]+"|'[^']+'|\S+)""")
SPLIT_ALL = re.compile(r"&&|\|\||;|\||\n")


def _cmd(ev):
    return (ev["_x"].get("input") or {}).get("command") or ""


def _ok(ev):
    return ev["outcome"] not in ("failed", "denied", "interrupted")


def _resolve(path, base):
    path = path.strip("\"'")
    if not is_abs(path) and base:
        path = posixpath.join(norm_path(base, lower=False), path.replace("\\", "/"))
    return posixpath.normpath(norm_path(path, lower=False))


def git_repos(ev):
    """(directory, segment) for every `git ...` segment in a shell command, following `cd` as it goes."""
    cur = ev["_x"].get("cwd")
    out = []
    for seg in SPLIT_ALL.split(_cmd(ev)):
        seg = seg.strip()
        m = CD.match(seg)
        if m:
            cur = _resolve(m.group(1), cur)
        elif re.match(r"git\b", seg):
            c = GIT_C.search(seg)
            repo = _resolve(c.group(1), cur) if c else cur
            if repo:
                out.append((repo, seg))
    return out


def _runs(events, claim_seq, rx):
    """Shell events before the claim whose command has a segment matching rx, never denied."""
    return [e for e in events if e["seq"] < claim_seq and e["tool"] in SHELL and e["outcome"] != "denied"
            and any(rx.search(s) for s in segments(_cmd(e)))]


def _check_run(events, claim, rx, what):
    runs = _runs(events, claim["event_seq"], rx)
    if not runs:
        return "unverified", f"no_{what}_run", []
    last = runs[-1]
    ev = [last["seq"]]
    if last["outcome"] == "failed":
        return "unverified", "last_run_failed", ev
    if last["outcome"] == "interrupted":
        return "unverified", "last_run_interrupted", ev
    if last["outcome"] != "ok":
        return "partial", "no_result_marker", ev
    # `is_masked(cmd)` only matters here: ok already required a success marker, never an exit code.
    for e in events:
        if last["seq"] < e["seq"] < claim["event_seq"] and e["tool"] and _ok(e) and (
            e["tool"] in EDIT_TOOLS or (e["tool"] in SHELL and SED_I.search(_cmd(e)))):
            return "partial", "files_changed_after_run", ev + [e["seq"]]
    return "observed", "result_marker_found" if is_masked(_cmd(last)) else "run_ok", ev


def _commit_sha(ev):
    for rx in SHA_OUT:
        m = rx.search(ev["_x"].get("result") or "")
        if m:
            return m.group(1)
    return None


def _check_commit(events, claim, git_block, git):
    cands = [e for e in _runs(events, claim["event_seq"], COMMIT_CMD)
             if e["outcome"] != "failed" and not any(
                 "--dry-run" in s or "commit-tree" in s for s in segments(_cmd(e)) if COMMIT_CMD.search(s))]
    if not cands:
        return "unverified", "no_commit_command", []
    want = claim["subject"]
    pick = next((e for e in reversed(cands)
                 if want and (_commit_sha(e) or "").startswith(want[:7])), cands[-1])
    sha = _commit_sha(pick) or want
    repo = next((r for r, seg in git_repos(pick) if COMMIT_CMD.search(seg)), pick["_x"].get("cwd"))
    root = git_block["repo_root"]
    if git_block["available"] and git_block["head_before"] and git_block["head_after"] and repo and under(repo, root):
        if git_block["head_before"] == git_block["head_after"]:
            return "unverified", "head_unchanged", [pick["seq"]]
        rng = git.commits_between(root, git_block["head_before"], git_block["head_after"])
        if sha and not any(c.startswith(sha) for c in rng):
            return "partial", "sha_not_in_session_range", [pick["seq"]]
        return "observed", "head_moved", [pick["seq"]]
    if sha and repo and git.commit_exists(repo, sha):
        return "observed", "sha_exists_in_repo", [pick["seq"]]
    return "partial", "commit_ran_head_not_checkable", [pick["seq"]]


def _match_paths(claim_path, cwd, paths):
    """Evidence paths matching a claimed path. Bare or relative claims also match by suffix."""
    cp = norm_path(claim_path)
    if is_abs(cp):
        return sorted(p for p in paths if p == cp)
    exact = _resolve(claim_path, cwd).lower()
    hit = sorted(p for p in paths if p == exact)
    tail = "/" + re.sub(r"^(?:\.{0,2}/)+", "", cp)  # lstrip("./") would also eat the dot of ".claude"
    return hit or sorted(p for p in paths if p.endswith(tail))


def _check_file(events, claim, git_block, changed, cwd_of):
    cwd = cwd_of
    a = {}
    for e in events:
        if e["seq"] < claim["event_seq"] and e["tool"] in EDIT_TOOLS and _ok(e):
            fp = (e["_x"].get("input") or {}).get("file_path") or (e["_x"].get("input") or {}).get("notebook_path")
            if fp:  # relative to the cwd of whoever made the edit (a subagent may sit in another worktree)
                a.setdefault(_resolve(fp, e["_x"].get("cwd")).lower(), []).append(e["seq"])
    root = git_block["repo_root"]
    b = set()
    if git_block["available"] and root:
        for rel in (git_block["status_after"] or []) + changed:
            b.add(norm_path(root + "/" + rel))
    ma, mb = _match_paths(claim["subject"], cwd, a), _match_paths(claim["subject"], cwd, b)
    seqs = sorted({s for p in ma for s in a[p]})
    if len({*ma, *mb}) > 1 and not is_abs(norm_path(claim["subject"])):
        return "partial", "ambiguous_path", seqs
    if ma and mb:
        return "observed", "tool_and_git", seqs
    if ma:
        if not git_block["available"] or not under(ma[0], root):
            return "observed", "tool_confirmed_only", seqs
        return "partial", "not_in_git_changes", seqs
    if mb:
        return "partial", "git_only", []
    return "unverified", "no_evidence", []


def check(all_events, claims, git_block, changed, git):
    for c in claims:
        t = c["type"]
        # A subagent's claim is judged on its own events; the parent's on everything, including subagent work.
        events = [e for e in all_events if e.get("agent") == c["agent"]] if c.get("agent") else all_events
        if t == "tests_pass":
            c["status"], c["reason"], c["evidence"] = _check_run(events, c, TEST_CMD, "test")
        elif t == "build_ok":
            c["status"], c["reason"], c["evidence"] = _check_run(events, c, BUILD_CMD, "build")
        elif t == "committed":
            c["status"], c["reason"], c["evidence"] = _check_commit(events, c, git_block, git)
        elif t == "file_change":
            cwd = next(e["_x"]["cwd"] for e in events if e["seq"] == c["event_seq"])
            c["status"], c["reason"], c["evidence"] = _check_file(events, c, git_block, changed, cwd)
        else:
            c["status"], c["reason"], c["evidence"] = "unverified", "other_claims_are_not_checked", []
        if c.pop("cap") and c["status"] == "observed":
            c["status"], c["reason"] = "partial", "claim_has_exception"
    return claims


def overall(claims):
    return min((c["status"] for c in claims), key=RANK.get) if claims else "unverified"
