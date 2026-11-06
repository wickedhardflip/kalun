"""Small shared helpers: secret scrubbing, Eastern time, path normalization."""
import re
from datetime import datetime, timedelta, timezone

_SCRUB = [
    re.compile(p)
    for p in (
        r"sk-[\w-]{20,}",
        r"gh[pousr]_\w{30,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[baprs]-[\w-]+",
        r"eyJ[\w-]+\.[\w-]+\.[\w-]+",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"Bearer \S+",
        r"://[^/\s:@]+:[^@\s]+@",
        # name, optional closing quote (JSON), separator, optional Basic/Bearer scheme, then a quoted or bare value
        r"""(?i)(?:password|passwd|pwd|token|secret|api[_-]?key|authorization|PGPASSWORD)["']?\s*[=:]\s*(?:(?:basic|bearer)\s+)?(?:"[^"]*"|'[^']*'|\S+)""",
        r"(?i)--(?:password|passwd|pass|token|secret|api-key)[ =]\S+",  # CLI flags
        r"(?i)(?<=\s)(?:-u|--user)\s+[^\s:]+:\S+",  # curl -u user:pass
    )
]


def scrub(text):
    for rx in _SCRUB:
        text = rx.sub("[REDACTED]", text)
    return text


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def to_et(s):
    """UTC ISO string -> aware datetime in US Eastern.

    Hand-rolled (second Sunday of March to first Sunday of November, 2:00 local)
    so Windows needs no tzdata package.
    """
    t = parse_ts(s).astimezone(timezone.utc)
    y = t.year
    start = _nth_sunday(y, 3, 2).replace(hour=7, tzinfo=timezone.utc)  # 2:00 EST
    end = _nth_sunday(y, 11, 1).replace(hour=6, tzinfo=timezone.utc)  # 2:00 EDT
    off = -4 if start <= t < end else -5
    return t.astimezone(timezone(timedelta(hours=off), "EDT" if off == -4 else "EST"))


def fmt_et(s, date=False):
    t = to_et(s)
    return t.strftime("%Y-%m-%d %H:%M:%S %Z") if date else t.strftime("%H:%M:%S")


def norm_path(p, lower=True):
    """Normalize a path: forward slashes, Git Bash /c/x fixed to C:/x, no trailing slash; lowercase to compare."""
    p = p.strip().strip("`'\"").replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])(/|$)", p)
    if m:
        p = m.group(1).upper() + ":" + p[2:]
    p = re.sub(r"/{2,}", "/", p).rstrip("/")
    return p.lower() if lower else p


def is_abs(p):
    return bool(re.match(r"^[a-z]:/", norm_path(p)))


def under(path, root):
    path, root = norm_path(path), norm_path(root)
    return path == root or path.startswith(root + "/")
