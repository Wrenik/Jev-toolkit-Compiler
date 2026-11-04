"""Command line: python -m jev_context {serve,ingest,search,get} ..."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .ingest import ingest_tree
from .search import DEFAULT_BUDGET_TOKENS, get_turn, search
from .store import connect, default_db

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def log_stats(db, path: str | None = None) -> str:
    """One line a person can read: how big the log is now."""
    sessions = db.execute("SELECT count(*) FROM session").fetchone()[0]
    turns, redactions = db.execute("SELECT count(*), COALESCE(sum(redactions),0) FROM turn").fetchone()
    size = f", database {Path(path).stat().st_size / 1e6:.1f} MB at {path}" if path and Path(path).exists() else ""
    return f"log now: {sessions} sessions, {turns} turns, {redactions} redactions{size}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jev_context")
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
    p.add_argument("--project")
    p.add_argument("--half-life", type=float, help="recency decay half-life in days")
    p.add_argument("--role", choices=["user", "assistant"])
    p.add_argument("--kind", choices=["text", "tool_use", "tool_result"])
    p.add_argument("--tool", help="tool name, e.g. Bash")
    p.add_argument("--explain", action="store_true", help="show the BM25 score (lower = better match)")
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
    a = ap.parse_args(argv)
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
    elif a.cmd == "setup":
        from .maintain import doctor, mcp_command

        print("this run:", ingest_tree(db, Path(a.root)))
        print(chr(10).join(doctor(db, a.db)))
        print(chr(10) + "Register the search tool with Claude Code (run once, then restart it):" + chr(10) + "  " + mcp_command(a.db))
    elif a.cmd == "doctor":
        from .maintain import doctor

        lines = doctor(db, a.db)
        print(chr(10).join(lines))
        return 1 if any(x.startswith("FAIL") for x in lines) else 0
    elif a.cmd == "status":
        from .maintain import status

        print(status(db, a.db))
    elif a.cmd == "forget":
        from .maintain import forget

        if not (a.session or a.project or a.everything):
            print("say what to forget: --session ID, --project NAME or --all")
            return 2
        if not a.yes:
            print("dry run: add --yes to delete. (Transcript files are never touched.)")
            return 0
        print(f"forgot {forget(db, a.session, a.project, a.everything)} turns")
    elif a.cmd == "tier":
        from .retention import purge_cold, warm

        print({"warmed": warm(db, a.warm_days), "purged": purge_cold(db, a.cold_days, a.enable_cold)})
    elif a.cmd == "search":
        since = (datetime.now() - timedelta(days=a.days)).timestamp() if a.days else None
        hits = search(db, a.query, a.limit, a.budget, since, a.project, a.half_life, role=a.role, kind=a.kind, tool=a.tool)
        if not hits:
            print("No matches. All words must appear; use fewer words, a prefix (word*), or \"an exact phrase\".")
        for h in hits:
            when = datetime.fromtimestamp(h.ts).strftime("%Y-%m-%d %H:%M") if h.ts else "?"
            score = f"  score {h.score:.2f}" if a.explain else ""
            print(f"{h.id}  {when}  {h.role}/{h.kind}{'/' + h.tool if h.tool else ''}{score}\n    {h.snippet}")
    else:
        for t in get_turn(db, a.id, a.around):
            print(f"--- {t['id']} {t['role']}/{t['kind']}{' (truncated)' if t['truncated'] else ''}\n{t['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
