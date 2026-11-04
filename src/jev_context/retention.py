"""Retention tiers for the long-term log. Nothing is deleted unless the cold tier is switched on.

hot   0..warm_days      everything as ingested.
warm  warm_days..cold   user/assistant text untouched; tool calls and results keep only a short stub in
                        the search index, their full redacted body sits compressed in `blob`
                        (`get_turn` still returns it in full).
cold  > cold_days       deleted, but only when the caller passes enabled=True. Off by default.

Both steps are idempotent and safe to run on every ingest.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

from .store import pack

DAY = 86400.0
WARM_DAYS = 30.0
COLD_DAYS = 730.0
STUB_CHARS = 200


def warm(db: sqlite3.Connection, warm_days: float = WARM_DAYS, now: float | None = None) -> int:
    """Shrink the indexed text of old tool turns. Returns how many turns changed."""
    cutoff = (time.time() if now is None else now) - warm_days * DAY
    rows = db.execute(
        "SELECT rowid, text, tool, blob_sha FROM turn "
        "WHERE ts IS NOT NULL AND ts < ? AND kind IN ('tool_use','tool_result') AND length(text) > ?",
        (cutoff, STUB_CHARS),
    ).fetchall()
    changed = 0
    with db:
        for r in rows:
            sha = r["blob_sha"]
            if sha is None:  # the stored text is still the whole body: keep it, compressed
                raw = r["text"].encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                codec, data = pack(raw)
                db.execute(
                    "INSERT OR IGNORE INTO blob(sha256, codec, size, data) VALUES (?,?,?,?)",
                    (sha, codec, len(raw), data),
                )
            stub = r["text"][:STUB_CHARS]
            db.execute(
                "INSERT INTO turn_fts(turn_fts, rowid, text, tool) VALUES ('delete', ?, ?, ?)",
                (r["rowid"], r["text"], r["tool"]),
            )
            db.execute("UPDATE turn SET text=?, blob_sha=? WHERE rowid=?", (stub, sha, r["rowid"]))
            db.execute("INSERT INTO turn_fts(rowid, text, tool) VALUES (?,?,?)", (r["rowid"], stub, r["tool"]))
            changed += 1
    return changed


def purge_cold(
    db: sqlite3.Connection, cold_days: float = COLD_DAYS, enabled: bool = False, now: float | None = None
) -> int:
    """Delete turns older than `cold_days`. Does nothing unless `enabled` is True."""
    if not enabled:
        return 0
    cutoff = (time.time() if now is None else now) - cold_days * DAY
    with db:
        n = db.execute("DELETE FROM turn WHERE ts IS NOT NULL AND ts < ?", (cutoff,)).rowcount
        db.execute("DELETE FROM blob WHERE sha256 NOT IN (SELECT blob_sha FROM turn WHERE blob_sha IS NOT NULL)")
        db.execute("DELETE FROM session WHERE id NOT IN (SELECT DISTINCT session_id FROM turn)")
    return n
