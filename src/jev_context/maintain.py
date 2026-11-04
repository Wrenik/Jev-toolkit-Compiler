"""Housekeeping for the log: status and forgetting. Nothing here talks to the network."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def status(db: sqlite3.Connection, path: str | Path | None = None) -> str:
    sessions = db.execute("SELECT count(*) FROM session").fetchone()[0]
    turns, redactions = db.execute("SELECT count(*), COALESCE(sum(redactions),0) FROM turn").fetchone()
    first, last = db.execute("SELECT min(ts), max(ts) FROM turn").fetchone()
    day = lambda x: datetime.fromtimestamp(x).strftime("%Y-%m-%d") if x else "-"  # noqa: E731
    lines = [f"{sessions} sessions, {turns} turns, {redactions} redactions, {day(first)} .. {day(last)}"]
    if path and Path(path).exists():
        lines.append(f"database: {Path(path).resolve()} ({Path(path).stat().st_size / 1e6:.1f} MB)")
    return "\n".join(lines)


def stats(db: sqlite3.Connection, path: str | Path | None = None, top: int = 15) -> str:
    """Where the log's size comes from: per project, per role/kind, top tools. Counts only, no transcript text."""
    day = lambda x: datetime.fromtimestamp(x).strftime("%Y-%m-%d") if x else "-"  # noqa: E731
    lines = [status(db, path), "", "projects (sessions, turns, last turn):"]
    rows = db.execute(
        "SELECT COALESCE(s.project,'?') p, count(DISTINCT s.id) n, count(t.rowid) c, max(t.ts) m FROM session s "
        "LEFT JOIN turn t ON t.session_id = s.id GROUP BY p ORDER BY c DESC LIMIT ?",
        (top,),
    ).fetchall()
    lines += [f"  {r['p']}: {r['n']} sessions, {r['c']} turns, {day(r['m'])}" for r in rows] or ["  (none)"]
    kinds = db.execute("SELECT role, kind, count(*) c FROM turn GROUP BY role, kind ORDER BY c DESC").fetchall()
    lines.append("by role/kind: " + (", ".join(f"{r['role']}/{r['kind']} {r['c']}" for r in kinds) or "-"))
    tools = db.execute(
        "SELECT tool, count(*) c FROM turn WHERE tool IS NOT NULL GROUP BY tool ORDER BY c DESC LIMIT 8"
    ).fetchall()
    lines.append("top tools: " + (", ".join(f"{r['tool']} {r['c']}" for r in tools) or "-"))
    big = db.execute("SELECT count(*), COALESCE(sum(size),0) FROM blob").fetchone()
    lines.append(f"full-body blobs: {big[0]} ({big[1] / 1e6:.1f} MB before compression)")
    return "\n".join(lines)


def forget(
    db: sqlite3.Connection, session: str | None = None, project: str | None = None, everything: bool = False
) -> int:
    """Delete turns of one session, one project, or everything. Returns the number of turns removed.

    The transcript file is not touched, and its recorded size/mtime stay, so the same unchanged file is
    NOT read in again; a transcript that Claude Code appends to later is re-read in full.
    """
    if not (session or project or everything):
        raise ValueError("say what to forget: a session, a project, or everything")
    where, args = (
        ("1=1", [])
        if everything
        else (
            ("session_id = ?", [session])
            if session
            else ("session_id IN (SELECT id FROM session WHERE project = ?)", [project])
        )
    )
    with db:
        n = db.execute(f"DELETE FROM turn WHERE {where}", args).rowcount
        db.execute("DELETE FROM blob WHERE sha256 NOT IN (SELECT blob_sha FROM turn WHERE blob_sha IS NOT NULL)")
        db.execute("DELETE FROM session WHERE id NOT IN (SELECT DISTINCT session_id FROM turn)")
    db.execute("INSERT INTO turn_fts(turn_fts) VALUES ('optimize')")
    db.commit()
    return n


def mcp_command(db_path: str | Path) -> str:
    """The exact `claude mcp add` line for this machine. Printed, never run: it changes Claude Code's config."""
    import sys

    q = lambda s: f'"{s}"' if " " in str(s) else str(s)  # noqa: E731
    return (
        f"claude mcp add jev-context -- {q(sys.executable)} -m jev_context.mcp_server --db {q(Path(db_path).resolve())}"
    )


def doctor(db: sqlite3.Connection, path: str | Path | None = None, port: int = 8765) -> list[str]:
    """One line per check, starting OK / WARN / FAIL, each WARN/FAIL with what to do about it."""
    import json
    import urllib.request

    from .search import search

    out = [status(db, path).splitlines()[0]]
    turns = db.execute("SELECT count(*) FROM turn").fetchone()[0]
    if turns == 0:
        out.append(
            "FAIL log is empty: run `python -m jev_context ingest` (Claude Code keeps transcripts under ~/.claude/projects)"
        )
    else:
        row = db.execute(
            "SELECT text FROM turn WHERE kind='text' AND length(text) > 8 ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        word = max(row["text"].split(), key=len) if row else ""
        found = bool(word) and bool(search(db, word, limit=1))
        out.append(
            "OK search works (looked up a word from the newest logged turn)"
            if found
            else "WARN search self-test found nothing; the index may need `python -m jev_context ingest`"
        )
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"X-Jev-Router": "1"})
        h = json.loads(urllib.request.urlopen(req, timeout=1).read())
        out.append(
            "OK local server is up"
            + ("" if h.get("has_context") else " but WITHOUT the context endpoints; restart Claude Code")
        )
    except Exception:  # noqa: BLE001
        out.append("WARN local server is not running (only needed for the advisor; starts with the jev-context mod)")
    return out
