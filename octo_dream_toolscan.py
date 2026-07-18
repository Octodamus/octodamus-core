"""
octo_dream_toolscan.py — fleet-wide tool-error "dreaming" pass.

Out-of-band immune system for silent, recurring failures. Scans recent logs across
the whole fleet (runner, API server, watchdog, ACP worker, and per-agent session
logs), groups error lines into normalized signatures, ranks by prevalence, and emails
the operator the recurring patterns with counts + a sample. This is the tool-call
scrutiny the in-band memory system can't do (no single session sees the pattern).

Motivating case: `tool_check_x402_revenue` KeyError fired every session for 31+
sessions and nothing caught it. A prevalence-ranked scan surfaces exactly that.

Wired into octo_memory_distill.run() (Sun/Wed/Sat). Also runnable standalone:
    python octo_dream_toolscan.py [--days 7] [--min 4] [--no-email]
"""
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
REPORT   = LOGS_DIR / "dream_toolscan_latest.md"

MAX_BYTES_PER_FILE = 8_000_000   # tail this many bytes from big rolling logs
MAX_LINES_PER_FILE = 60_000

# A line is a candidate error if it matches this and NOT the benign exclusions.
_ERROR_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|KeyError|ValueError|TypeError|"
    r"AttributeError|NameError|IndexError|Unauthorized|invalid_grant|RefreshError|"
    r"actively refused|CRITICAL|ModuleNotFound|no such|not found|disabled|"
    r"500 internal|internal server error|\b50[023]\b|timed out|timeout|could not|"
    r"unable to|permission denied|connection refused)\b",
    re.IGNORECASE,
)
# Benign / handled / high-noise lines that are NOT real failures — keep them out.
_BENIGN_RE = re.compile(
    r"(secrets loaded from cache|no error|✅|PASS\b|-> PASS|0 repl|Done\.|"
    r"no calls ready|No expired calls|not STRONG|already posted|No Oracle call found|"
    r"Backup complete|no new resolved|HTTP/1\.1 451|HTTP/1\.1 200|429 \"|"
    r"code=429|rate.?limit|bind on address|address already in use|"  # known-handled infra churn
    r"actively refused it\.\" connIndex|Terminating session)",
    re.IGNORECASE,
)


# Pure-infrastructure logs whose "errors" are network churn, not tool/app bugs.
_SKIP_FILES = ("cloudflared", "botcoin_miner")

_LINE_DATE_RE = re.compile(r"(\d{4}-\d\d-\d\d)")                 # ISO date at/near start
_LINE_MDY_RE  = re.compile(r"^\[(\d\d)/(\d\d)/(\d\d) ")          # [MM/DD/YY ...] rich format


def _line_date(line: str) -> str | None:
    """Extract a YYYY-MM-DD date from a log line, or None if not parseable."""
    m = _LINE_DATE_RE.match(line) or _LINE_DATE_RE.match(line[:2] and line.lstrip("[") or "")
    if m:
        return m.group(1)
    m = _LINE_MDY_RE.match(line)
    if m:
        mm, dd, yy = m.groups()
        return f"20{yy}-{mm}-{dd}"
    return None


def _iter_error_lines(days: int):
    """Yield (source_name, raw_line) for candidate error lines in recent logs.

    Recency is enforced on each LINE's own timestamp (rolling logs contain months of
    history in their tail), not just the file mtime.
    """
    from datetime import date, timedelta
    cutoff_mtime = time.time() - days * 86400
    cutoff_date  = (date.today() - timedelta(days=days)).isoformat()
    sources = list(LOGS_DIR.glob("*.log")) + list(BASE_DIR.glob(".agents/*/*.log"))
    for f in sources:
        try:
            if any(s in f.name for s in _SKIP_FILES):
                continue
            if f.stat().st_mtime < cutoff_mtime:
                continue
            size = f.stat().st_size
            with f.open("r", encoding="utf-8", errors="ignore") as fh:
                if size > MAX_BYTES_PER_FILE:
                    fh.seek(size - MAX_BYTES_PER_FILE)
                    fh.readline()  # discard partial first line
                for i, line in enumerate(fh):
                    if i > MAX_LINES_PER_FILE:
                        break
                    if not (_ERROR_RE.search(line) and not _BENIGN_RE.search(line)):
                        continue
                    ld = _line_date(line)
                    if ld is not None and ld < cutoff_date:
                        continue  # old line in a rolling log
                    yield f.name, line.rstrip("\n")
        except OSError:
            continue


def _signature(line: str) -> str:
    """Collapse a log line to a stable signature so recurring errors group together."""
    s = line
    s = re.sub(r"^\[?\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[.,\d]*\+?[\d:]*\]?", "", s)  # ISO ts
    s = re.sub(r"^\[\d\d/\d\d/\d\d \d\d:\d\d:\d\d\]", "", s)                          # bracket ts
    s = re.sub(r"\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]", "", s)
    s = re.sub(r"0x[0-9a-fA-F]+", "0x#", s)
    s = re.sub(r"\b[0-9a-fA-F]{16,}\b", "#hash", s)
    s = re.sub(r"[A-Za-z]:\\[^\s'\"]+", "#path", s)
    s = re.sub(r"/[^\s'\"]{3,}", "#path", s)
    s = re.sub(r"\b\d[\d,\.]*\b", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:220]


def scan(days: int = 7, min_count: int = 4) -> list[dict]:
    """Return recurring error signatures (count >= min_count), most prevalent first."""
    agg: dict[str, dict] = {}
    for src, line in _iter_error_lines(days):
        sig = _signature(line)
        if len(sig) < 8:
            continue
        e = agg.setdefault(sig, {"count": 0, "sources": set(), "sample": line.strip()[:300]})
        e["count"] += 1
        e["sources"].add(src)
    findings = [
        {"signature": sig, "count": e["count"], "n_sources": len(e["sources"]),
         "sources": sorted(e["sources"])[:6], "sample": e["sample"]}
        for sig, e in agg.items() if e["count"] >= min_count
    ]
    findings.sort(key=lambda x: (x["count"], x["n_sources"]), reverse=True)
    return findings


def build_report(findings: list[dict], days: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not findings:
        return f"# Dream tool-scan — {now}\n\nNo recurring tool errors (last {days} days). Fleet clean.\n"
    lines = [f"# Dream tool-scan — {now}",
             f"\n{len(findings)} recurring error pattern(s) across the fleet (last {days} days), "
             f"ranked by prevalence. Review the top ones — a high count across sessions usually "
             f"means a silent bug (like the 31-session revenue KeyError).\n"]
    for i, f in enumerate(findings[:20], 1):
        lines.append(f"## {i}. ×{f['count']}  ({f['n_sources']} log source(s))")
        lines.append(f"- Sources: {', '.join(f['sources'])}")
        lines.append(f"- Sample: `{f['sample']}`\n")
    return "\n".join(lines)


def run(days: int = 7, min_count: int = 4, email: bool = True) -> list[dict]:
    findings = scan(days=days, min_count=min_count)
    report = build_report(findings, days)
    try:
        REPORT.write_text(report, encoding="utf-8")
    except OSError:
        pass
    print(f"[DreamScan] {len(findings)} recurring error pattern(s) over {days}d.")
    for f in findings[:8]:
        print(f"  x{f['count']:<4} [{f['n_sources']} src] {f['sample'][:90]}")
    if email and findings:
        try:
            from octo_notify import _send
            top = findings[:15]
            body = [f"Fleet dream-scan found {len(findings)} recurring error pattern(s) "
                    f"in the last {days} days (top {len(top)} below). "
                    f"High count across sessions = likely silent bug.\n"]
            for i, f in enumerate(top, 1):
                body.append(f"{i}. x{f['count']} ({f['n_sources']} sources: {', '.join(f['sources'])})\n"
                            f"   {f['sample']}\n")
            _send(f"Octodamus Dream Scan — {len(findings)} recurring errors", "\n".join(body))
            print("[DreamScan] Emailed operator.")
        except Exception as e:
            print(f"[DreamScan] Email failed: {e}")
    return findings


if __name__ == "__main__":
    _days, _min, _email = 7, 4, True
    for a in sys.argv[1:]:
        if a.startswith("--days="): _days = int(a.split("=", 1)[1])
        elif a == "--days" : pass
        elif a.startswith("--min="): _min = int(a.split("=", 1)[1])
        elif a == "--no-email": _email = False
    # support "--days 7" form too
    _av = sys.argv[1:]
    for i, a in enumerate(_av):
        if a == "--days" and i + 1 < len(_av): _days = int(_av[i + 1])
        if a == "--min" and i + 1 < len(_av): _min = int(_av[i + 1])
    run(days=_days, min_count=_min, email=_email)
