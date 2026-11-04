import json
import time

from jev_context.ingest import ingest_lines
from jev_context.retention import DAY, STUB_CHARS, purge_cold, warm
from jev_context.search import get_turn, search
from jev_context.store import connect

NOW = 1_800_000_000.0


def rec(uuid, content, age_days, typ="assistant"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age_days * DAY))
    return json.dumps(
        {"type": typ, "uuid": uuid, "sessionId": "s" + uuid, "timestamp": ts, "message": {"content": content}}
    )


def make():
    db = connect(":memory:")
    big = "quokka " + "filler " * 200 + " deepneedle"
    ingest_lines(
        db,
        [
            rec("old", [{"type": "tool_result", "tool_use_id": "t", "content": big}], 90, typ="user"),
            rec("new", [{"type": "tool_result", "tool_use_id": "t", "content": big}], 2, typ="user"),
            rec("chat", [{"type": "text", "text": big}], 90),
            rec("ancient", [{"type": "text", "text": "marmot note"}], 800),
        ],
        project="p",
    )
    return db


def test_warm_shrinks_only_old_tool_turns_and_keeps_full_body():
    db = make()
    assert warm(db, now=NOW) == 1
    old = db.execute("SELECT text FROM turn WHERE id='old:0'").fetchone()[0]
    assert len(old) <= STUB_CHARS
    assert db.execute("SELECT length(text) FROM turn WHERE id='new:0'").fetchone()[0] > STUB_CHARS
    assert db.execute("SELECT length(text) FROM turn WHERE id='chat:0'").fetchone()[0] > STUB_CHARS
    assert "deepneedle" in get_turn(db, "old:0")[0]["text"]  # still in full via get_turn


def test_warm_updates_the_index_and_is_idempotent():
    db = make()
    warm(db, now=NOW)
    ids = {h.id for h in search(db, "quokka")}
    assert "old:0" in ids  # the stub still holds the head
    assert "old:0" not in {h.id for h in search(db, "deepneedle")}
    assert warm(db, now=NOW) == 0
    db.execute("INSERT INTO turn_fts(turn_fts) VALUES ('integrity-check')")


def test_cold_is_off_by_default_and_deletes_when_enabled():
    db = make()
    assert purge_cold(db, now=NOW) == 0
    assert db.execute("SELECT count(*) FROM turn").fetchone()[0] == 4
    assert purge_cold(db, enabled=True, now=NOW) == 1
    assert search(db, "marmot") == []
    assert db.execute("SELECT count(*) FROM session").fetchone()[0] == 3
    db.execute("INSERT INTO turn_fts(turn_fts) VALUES ('integrity-check')")
