"""SQLite + FTS5 store for the long-term log. Local only; text is redacted before it gets here."""

from __future__ import annotations

import hashlib
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.14+; older versions fall back to zlib (the codec is stored per blob).
    from compression import zstd
except ImportError:  # pragma: no cover
    zstd = None

# Level 19 made ingest about a third slower on a 1 GB test for little extra saving; 6 is the speed/size knee.
ZSTD_LEVEL = 6


# `turn.text` keeps this much of each end of a long body; the part between is indexed in chunks instead.
HEAD_TAIL = 4_000
CHUNK_CHARS = 1_500
MAX_CHUNKS = 400  # per turn: about 600 000 characters; the rest stays reachable by get_turn paging, just not searchable


def default_db() -> Path:
    """``<JEV_HOME>/var/log/jev-context.sqlite`` (see ``jev_core.config``)."""
    from jev_core.config import var_path

    return var_path() / "log" / "jev-context.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY, project TEXT, first_ts REAL, last_ts REAL, model TEXT
);
CREATE TABLE IF NOT EXISTS turn (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,           -- "<record uuid>:<block index>": the idempotency key
    session_id TEXT NOT NULL REFERENCES session(id),
    seq INTEGER NOT NULL,              -- file line * 1000 + block index: stable across re-ingests
    ts REAL,
    role TEXT NOT NULL,                -- user | assistant
    kind TEXT NOT NULL,                -- text | tool_use | tool_result
    tool TEXT,
    text TEXT NOT NULL,                -- redacted; long bodies are clipped to head+tail
    redactions INTEGER NOT NULL DEFAULT 0,
    blob_sha TEXT                      -- full redacted body in blob, when clipped
);
CREATE INDEX IF NOT EXISTS turn_session ON turn(session_id, seq);
CREATE TABLE IF NOT EXISTS blob (
    sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS file_state (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS turn_fts USING fts5(
    text, tool, content='turn', content_rowid='rowid', tokenize='unicode61'
);
-- The middle of a long body (the part `turn.text` clips away), in pieces, so a word anywhere in a big tool result is searchable.
CREATE TABLE IF NOT EXISTS chunk (
    rowid INTEGER PRIMARY KEY, turn_rowid INTEGER NOT NULL, off INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunk_turn ON chunk(turn_rowid);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(text, content='chunk', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS chunk_ai AFTER INSERT ON chunk BEGIN
    INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunk_ad AFTER DELETE ON chunk BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS turn_ai AFTER INSERT ON turn BEGIN
    INSERT INTO turn_fts(rowid, text, tool) VALUES (new.rowid, new.text, new.tool);
END;
CREATE TRIGGER IF NOT EXISTS turn_ad AFTER DELETE ON turn BEGIN
    INSERT INTO turn_fts(turn_fts, rowid, text, tool) VALUES ('delete', old.rowid, old.text, old.tool);
END;
"""


@dataclass(frozen=True)
class NewTurn:
    id: str
    session_id: str
    seq: int
    ts: float | None
    role: str
    kind: str
    text: str
    tool: str | None = None
    redactions: int = 0
    full_text: str | None = None  # set when `text` is a clipped version of it


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = default_db() if path is None else path
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)  # wait for another writer (CLI ingest vs the server) instead of failing
    db.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        db.execute("PRAGMA journal_mode=WAL")  # the MCP server reads while an ingest writes
    db.executescript(SCHEMA)
    if not any(r[1] == "mtime_ns" for r in db.execute("PRAGMA table_info(file_state)")):
        db.execute("ALTER TABLE file_state ADD COLUMN mtime_ns INTEGER")  # logs made before this column existed
    return db


def pack(data: bytes) -> tuple[str, bytes]:
    if zstd is not None:
        return "zstd", zstd.compress(data, level=ZSTD_LEVEL)
    return "zlib", zlib.compress(data, 9)


def unpack(codec: str, data: bytes) -> bytes:
    if codec == "zstd":
        if zstd is None:  # pragma: no cover
            raise RuntimeError("blob was written with zstd; this Python has no compression.zstd")
        return zstd.decompress(data)
    return zlib.decompress(data)


def upsert_session(db: sqlite3.Connection, sid: str, project: str, ts: float | None, model: str | None) -> None:
    db.execute(
        "INSERT INTO session(id, project, first_ts, last_ts, model) VALUES (?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "first_ts=MIN(COALESCE(first_ts, excluded.first_ts), COALESCE(excluded.first_ts, first_ts)), "
        "last_ts=MAX(COALESCE(last_ts, excluded.last_ts), COALESCE(excluded.last_ts, last_ts)), "
        "model=COALESCE(excluded.model, model)",
        (sid, project, ts, ts, model),
    )


def add_turn(db: sqlite3.Connection, t: NewTurn) -> bool:
    """Insert one turn; returns False when its id is already stored (idempotent)."""
    sha = None
    if t.full_text is not None:
        raw = t.full_text.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
    cur = db.execute(
        "INSERT OR IGNORE INTO turn(id, session_id, seq, ts, role, kind, tool, text, redactions, blob_sha) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (t.id, t.session_id, t.seq, t.ts, t.role, t.kind, t.tool, t.text, t.redactions, sha),
    )
    if cur.rowcount and sha is not None:
        codec, data = pack(raw)
        db.execute(
            "INSERT OR IGNORE INTO blob(sha256, codec, size, data) VALUES (?,?,?,?)", (sha, codec, len(raw), data)
        )
        add_chunks(db, cur.lastrowid, t.full_text or "")
    return bool(cur.rowcount)


def chunk_pieces(full: str) -> list[tuple[int, str]]:
    """(offset, text) pieces of the middle of a long body, cut at a line break where there is one close by."""
    out: list[tuple[int, str]] = []
    pos, end = HEAD_TAIL, len(full) - HEAD_TAIL
    while pos < end and len(out) < MAX_CHUNKS:
        stop = min(end, pos + CHUNK_CHARS)
        if stop < end:
            nl = full.rfind("\n", pos + CHUNK_CHARS // 2, stop)
            stop = nl + 1 if nl > 0 else stop
        out.append((pos, full[pos:stop]))
        pos = stop
    return out


def add_chunks(db: sqlite3.Connection, turn_rowid: int | None, full: str) -> int:
    if turn_rowid is None or len(full) <= 2 * HEAD_TAIL:
        return 0
    pieces = chunk_pieces(full)
    db.executemany(
        "INSERT INTO chunk(turn_rowid, off, text) VALUES (?,?,?)", [(turn_rowid, off, text) for off, text in pieces]
    )
    return len(pieces)


def reindex_chunks(db: sqlite3.Connection, limit: int | None = None) -> int:
    """Chunk long turns that were ingested before chunks existed. Idempotent; returns how many turns were done."""
    rows = db.execute(
        "SELECT t.rowid, t.blob_sha FROM turn t WHERE t.blob_sha IS NOT NULL AND length(t.text) > 1000 "
        "AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.turn_rowid = t.rowid) LIMIT ?",
        (limit if limit is not None else -1,),
    ).fetchall()
    done = 0
    with db:
        for r in rows:
            full = get_blob(db, r["blob_sha"])
            if full and add_chunks(db, r["rowid"], full):
                done += 1
    return done


def get_blob(db: sqlite3.Connection, sha: str) -> str | None:
    row = db.execute("SELECT codec, data FROM blob WHERE sha256=?", (sha,)).fetchone()
    return unpack(row["codec"], row["data"]).decode("utf-8") if row else None
