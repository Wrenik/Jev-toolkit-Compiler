"""Search the long-term log. FTS5 + BM25, short snippets, hard cap on the size of an answer.

The cap matters: a search result lands in the model's context, so an unbounded one
could undo the savings the rest of this project is about. Full text is only returned
by `get_turn`, one turn (plus a few neighbours) at a time.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

from .store import get_blob

# Rough size estimate; the same 4-chars-per-token rule the rest of the toolkit uses.
CHARS_PER_TOKEN = 4
DEFAULT_BUDGET_TOKENS = 1_500
SNIPPET_CHARS = 240


@dataclass(frozen=True)
class Hit:
    id: str
    session_id: str
    ts: float | None
    role: str
    kind: str
    tool: str | None
    snippet: str
    score: float  # lower is better (BM25 as reported by SQLite, possibly recency-adjusted)


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word quoted, joined with AND.

    Raw user text may contain FTS operators or quotes that would raise a syntax error.
    """
    parts = []
    # "two words" in double quotes is an exact phrase; a trailing * on a word means prefix (zeb* finds zebra).
    for phrase, word in re.findall(r'"([^"]*)"|(\w+\*?)', text, flags=re.UNICODE):
        if phrase:
            inner = re.findall(r"\w+", phrase, flags=re.UNICODE)
            if inner:
                parts.append('"' + " ".join(inner) + '"')
        elif word:
            parts.append(f'"{word.rstrip("*")}"' + ("*" if word.endswith("*") else ""))
    return " ".join(parts)


def _filters(since, project, role, kind, tool) -> tuple[str, list]:
    sql, args = "", []
    for clause, value in ((" AND t.ts >= ?", since), (" AND s.project = ?", project), (" AND t.role = ?", role),
                          (" AND t.kind = ?", kind), (" AND t.tool = ?", tool)):
        if value is not None:
            sql += clause
            args.append(value)
    return sql, args


def count_hits(db: sqlite3.Connection, query: str, since: float | None = None, project: str | None = None,
               role: str | None = None, kind: str | None = None, tool: str | None = None) -> int:
    """How many turns match, ignoring limit and budget."""
    q = fts_query(query)
    if not q:
        return 0
    sql = ("SELECT count(*) FROM turn_fts JOIN turn t ON t.rowid = turn_fts.rowid "
           "JOIN session s ON s.id = t.session_id WHERE turn_fts MATCH ?")
    extra, more = _filters(since, project, role, kind, tool)
    return db.execute(sql + extra, [q, *more]).fetchone()[0]


def search(
    db: sqlite3.Connection,
    query: str,
    limit: int = 10,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    since: float | None = None,
    project: str | None = None,
    half_life_days: float | None = None,
    now: float | None = None,
    role: str | None = None,
    kind: str | None = None,
    tool: str | None = None,
) -> list[Hit]:
    """Best matches first. Stops early when the snippets would exceed `budget_tokens`.

    `half_life_days` turns on recency decay: a hit that old counts half as strong.
    """
    q = fts_query(query)
    if not q:
        return []
    sql = (
        "SELECT t.id, t.session_id, t.ts, t.role, t.kind, t.tool, bm25(turn_fts) AS score, "
        "snippet(turn_fts, 0, '[', ']', ' ... ', 32) AS snip "
        "FROM turn_fts JOIN turn t ON t.rowid = turn_fts.rowid "
        "JOIN session s ON s.id = t.session_id WHERE turn_fts MATCH ?"
    )
    extra, more = _filters(since, project, role, kind, tool)
    sql += extra
    args: list = [q, *more]
    # Fetch more than `limit` when decay may reorder results.
    sql += " ORDER BY score LIMIT ?"
    args.append(limit * 5 if half_life_days else limit)
    rows = db.execute(sql, args).fetchall()

    now = time.time() if now is None else now
    hits = []
    for r in rows:
        score = r["score"]  # BM25 in SQLite is negative; more negative = better
        if half_life_days and r["ts"] is not None:
            age_days = max(0.0, (now - r["ts"]) / 86400)
            score *= 0.5 ** (age_days / half_life_days)
        hits.append(Hit(r["id"], r["session_id"], r["ts"], r["role"], r["kind"], r["tool"],
                        r["snip"][:SNIPPET_CHARS], score))
    hits.sort(key=lambda h: h.score)

    out: list[Hit] = []
    used = 0
    for h in hits[:limit]:
        cost = (len(h.snippet) + 80) // CHARS_PER_TOKEN  # snippet plus the id/metadata line
        if out and used + cost > budget_tokens:
            break
        out.append(h)
        used += cost
    return out


def get_turn(db: sqlite3.Connection, turn_id: str, around: int = 0, max_chars: int = 6_000) -> list[dict]:
    """The turn and up to `around` neighbours on each side, in order. Long bodies come from the blob, capped."""
    row = db.execute("SELECT session_id, seq FROM turn WHERE id=?", (turn_id,)).fetchone()
    if row is None:
        return []
    rows = db.execute(
        "SELECT * FROM (SELECT * FROM turn WHERE session_id=? AND seq<=? ORDER BY seq DESC LIMIT ?) "
        "UNION SELECT * FROM (SELECT * FROM turn WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?) ORDER BY seq",
        (row["session_id"], row["seq"], around + 1, row["session_id"], row["seq"], around),
    ).fetchall()
    result = []
    for r in rows:
        body = (get_blob(db, r["blob_sha"]) if r["blob_sha"] and r["id"] == turn_id else None) or r["text"]  # blob gone: show the clipped text
        truncated = len(body) > max_chars
        result.append({"id": r["id"], "ts": r["ts"], "role": r["role"], "kind": r["kind"], "tool": r["tool"],
                       "text": body[:max_chars], "truncated": truncated})
    return result
