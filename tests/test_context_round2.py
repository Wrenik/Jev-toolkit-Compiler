import json

from jev_context.__main__ import main
from jev_context.ingest import ingest_lines
from jev_context.maintain import stats
from jev_context.mcp_server import TOOLS, call_tool, open_readonly
from jev_context.search import HIT_CLOSE, HIT_OPEN, no_match_hint, search
from jev_context.store import connect

# Built from parts so no scanner mistakes this file for a leak. Shapes only; none is a real credential.
NPM = "npm_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
CURL = "curl -u admin:" + "s3cretpassw0rd https://example.test/api"
FULLWIDTH = "jane.doe＠example.com"
WRAPPED = "token [word] <b>x</b>"


def rec(uuid, text, ts="2026-10-01T10:00:00.000Z"):
    return json.dumps({"type": "user", "uuid": uuid, "sessionId": "s1", "timestamp": ts, "message": {"content": text}})


def make(*texts):
    db = connect(":memory:")
    ingest_lines(db, [rec(f"u{i}", t) for i, t in enumerate(texts)], project="p")
    return db


def stored(db):
    return " ".join(r["text"] for r in db.execute("SELECT text FROM turn"))


def test_redaction_gaps_stay_closed():
    db = make(f"use {NPM} now", f"run {CURL}", f"mail {FULLWIDTH} please")
    text = stored(db)
    assert "A1b2C3d4E5f6" not in text
    assert "s3cretpassw0rd" not in text
    assert "doe" not in text and "example.com" not in text
    assert db.execute("SELECT sum(redactions) FROM turn").fetchone()[0] >= 3


def test_hit_marker_is_not_a_bracket():
    db = make(WRAPPED)
    snip = search(db, "token")[0].snippet
    assert HIT_OPEN + "token" + HIT_CLOSE in snip and "[word]" in snip


def test_pagination_offset_pages_do_not_overlap():
    db = make(*[f"zebra number {i}" for i in range(6)])
    first = search(db, "zebra", limit=3)
    second = search(db, "zebra", limit=3, offset=3)
    assert len(first) == len(second) == 3 and not {h.id for h in first} & {h.id for h in second}
    assert search(db, "zebra", limit=3, offset=6) == []


def test_empty_result_messages_say_why():
    assert "log is empty" in no_match_hint(connect(":memory:"), "zebra")
    db = make("zebra here")
    assert "loosen a filter" in no_match_hint(db, "zebra", project="other", filters={})
    assert "All words must appear" in no_match_hint(db, "unicorn")


def test_mcp_tools_are_read_only_and_page(tmp_path):
    assert all(t["annotations"]["readOnlyHint"] is True for t in TOOLS)
    path = tmp_path / "l.sqlite"
    db = connect(path)
    ingest_lines(db, [rec(f"u{i}", f"zebra {i}") for i in range(4)], project="p")
    db.commit()
    db.close()
    ro = open_readonly(path)
    out = call_tool(ro, "search_log", {"query": "zebra", "limit": 2})
    assert "showing 2 of 4" in out and "offset 2" in out
    assert "showing 3-4 of 4" in call_tool(ro, "search_log", {"query": "zebra", "limit": 2, "offset": 2})
    assert "past the end" in call_tool(ro, "search_log", {"query": "zebra", "offset": 50})
    assert "other filters" not in call_tool(ro, "search_log", {"query": "zebra", "project": "nope"})


def test_cli_stats_since_and_bad_date(tmp_path, capsys):
    path = tmp_path / "l.sqlite"
    db = connect(path)
    ingest_lines(db, [rec("u1", "zebra")], project="p")
    db.commit()
    db.close()
    assert "p: 1 sessions, 1 turns" in stats(connect(path))
    assert main(["--db", str(path), "stats"]) == 0
    assert main(["--db", str(path), "search", "zebra", "--since", "2026-01-01"]) == 0
    assert "zebra" in capsys.readouterr().out
    assert main(["--db", str(path), "search", "zebra", "--since", "bad"]) == 2
