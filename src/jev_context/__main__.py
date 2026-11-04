"""Command line: python -m jev_context {serve,ingest,search,get} ..."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .ingest import ingest_tree
from .search import DEFAULT_BUDGET_TOKENS, get_turn, search
from .store import connect, default_db

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("jev-toolkit-compiler")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown (not installed)"


def log_stats(db, path: str | None = None) -> str:
    """One line a person can read: how big the log is now."""
    sessions = db.execute("SELECT count(*) FROM session").fetchone()[0]
    turns, redactions = db.execute("SELECT count(*), COALESCE(sum(redactions),0) FROM turn").fetchone()
    size = f", database {Path(path).stat().st_size / 1e6:.1f} MB at {path}" if path and Path(path).exists() else ""
    return f"log now: {sessions} sessions, {turns} turns, {redactions} redactions{size}"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jev_context")
    ap.add_argument("--version", action="version", version=f"jev_context {_version()}")
    ap.add_argument("--db", default=None, help="log database; default: JEV_HOME/var/log/jev-context.sqlite")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="local server for the Claude Code mod")
    p.add_argument("--port", type=int)
    p = sub.add_parser("ingest", help="read transcripts into the log (idempotent)")
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--dry-run", action="store_true", help="parse everything in memory and report; the log is not touched")
    p = sub.add_parser("tier", help="apply retention: shrink old tool turns; optional cold purge")
    p.add_argument("--warm-days", type=float, default=30.0)
    p.add_argument("--cold-days", type=float, default=730.0)
    p.add_argument("--enable-cold", action="store_true", help="DELETE turns older than --cold-days (off by default)")
    p = sub.add_parser("search", help="full-text search; prints short snippets")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS, help="max tokens of output")
    p.add_argument("--days", type=float, help="only the last N days")
    p.add_argument("--since", help="only turns on or after this date, YYYY-MM-DD")
    p.add_argument("--offset", type=int, default=0, help="skip this many matches (next page)")
    p.add_argument("--project")
    p.add_argument("--half-life", type=float, help="recency decay half-life in days")
    p.add_argument("--role", choices=["user", "assistant"])
    p.add_argument("--kind", choices=["text", "tool_use", "tool_result"])
    p.add_argument("--tool", help="tool name, e.g. Bash")
    p.add_argument("--json", action="store_true", dest="as_json", help="one JSON object per line, for scripts")
    p.add_argument("--explain", action="store_true", help="show the BM25 score (lower = better match)")
    sub.add_parser("stats", help="per-project breakdown: sessions, turns, dates, top tools")
    sub.add_parser("status", help="what the log holds and where it lives")
    p = sub.add_parser("setup", help="ingest, then check everything and print the one command that registers the MCP server")
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    sub.add_parser("doctor", help="check the log, search and the local server; says what to fix")
    p = sub.add_parser("forget", help="delete logged turns (the transcript files are not touched)")
    p.add_argument("--session")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true", dest="everything")
    p.add_argument("--yes", action="store_true", help="required; without it only the count is shown")
    p = sub.add_parser("get", help="show one turn in full, with neighbours")
    p.add_argument("id")
    p.add_argument("--around", type=int, default=0)
    return ap


def _since(a) -> float | None:
    if a.since:
        return datetime.strptime(a.since, "%Y-%m-%d").timestamp()
    return (datetime.now() - timedelta(days=a.days)).timestamp() if a.days else None


def _cmd_search(db, a) -> int:
    from .search import no_match_hint

    try:
        since = _since(a)
    except ValueError:
        print("--since must look like 2026-01-31")
        return 2
    flt = {k: v for k, v in (("role", a.role), ("kind", a.kind), ("tool", a.tool)) if v}
    hits = search(db, a.query, a.limit, a.budget, since, a.project, a.half_life, offset=a.offset, **flt)
    if not hits and not a.as_json:
        print(no_match_hint(db, a.query, since, a.project, flt))
    for h in hits:
        if a.as_json:
            print(json.dumps({"id": h.id, "session": h.session_id, "ts": h.ts, "role": h.role, "kind": h.kind,
                              "tool": h.tool, "snippet": h.snippet, "score": h.score}, ensure_ascii=False))
            continue
        when = datetime.fromtimestamp(h.ts).strftime("%Y-%m-%d %H:%M") if h.ts else "?"
        score = f"  score {h.score:.2f}" if a.explain else ""
        print(f"{h.id}  {when}  {h.role}/{h.kind}{'/' + h.tool if h.tool else ''}{score}\n    {h.snippet}")
    return 0


def _cmd_forget(db, a) -> int:
    from .maintain import forget

    if not (a.session or a.project or a.everything):
        print("say what to forget: --session ID, --project NAME or --all")
        return 2
    if not a.yes:
        print("dry run: add --yes to delete. (Transcript files are never touched.)")
        return 0
    print(f"forgot {forget(db, a.session, a.project, a.everything)} turns")
    return 0


def _cmd_doctor(db, a) -> int:
    from .maintain import doctor

    lines = doctor(db, a.db)
    print(chr(10).join(lines))
    return 1 if any(x.startswith("FAIL") for x in lines) else 0


def _cmd_setup(db, a) -> int:
    from .maintain import doctor, mcp_command

    print("this run:", ingest_tree(db, Path(a.root)))
    print(chr(10).join(doctor(db, a.db)))
    print(chr(10) + "Register the search tool with Claude Code (run once, then restart it):" + chr(10) + "  " + mcp_command(a.db))
    return 0


def _cmd_tier(db, a) -> int:
    from .retention import purge_cold, warm

    print({"warmed": warm(db, a.warm_days), "purged": purge_cold(db, a.cold_days, a.enable_cold)})
    return 0


def _cmd_get(db, a) -> int:
    for t in get_turn(db, a.id, a.around):
        print(f"--- {t['id']} {t['role']}/{t['kind']}{' (truncated)' if t['truncated'] else ''}\n{t['text']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a Windows console is not UTF-8; never crash on a character it lacks
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    a.db = a.db or str(default_db())
    if a.cmd == "serve":
        from .server import DEFAULT_PORT, serve

        serve(a.port or DEFAULT_PORT)
        return 0
    if a.cmd == "ingest" and a.dry_run:
        # Everything goes into a throwaway in-memory log, so the counts are "what a first run would add".
        mem = connect(":memory:")
        print("dry run (nothing written):", ingest_tree(mem, Path(a.root)))
        print(log_stats(mem))
        return 0
    db = connect(a.db)
    if a.cmd == "ingest":
        print("this run:", ingest_tree(db, Path(a.root)))
        print(log_stats(db, a.db))
        return 0
    if a.cmd == "status":
        from .maintain import status

        print(status(db, a.db))
        return 0
    if a.cmd == "stats":
        from .maintain import stats

        print(stats(db, a.db))
        return 0
    handlers = {"setup": _cmd_setup, "doctor": _cmd_doctor, "forget": _cmd_forget, "tier": _cmd_tier,
                "search": _cmd_search, "get": _cmd_get}
    return handlers[a.cmd](db, a)


if __name__ == "__main__":
    raise SystemExit(main())
