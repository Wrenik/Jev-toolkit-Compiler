import json

from jev_context.ingest import ingest_lines
from jev_context.maintain import forget, status
from jev_context.search import fts_query, search
from jev_context.store import connect


def rec(typ, uuid, content, sid="s1"):
    return json.dumps(
        {
            "type": typ,
            "uuid": uuid,
            "sessionId": sid,
            "timestamp": "2026-10-01T10:00:00Z",
            "message": {"content": content},
        }
    )


def make():
    db = connect(":memory:")
    ingest_lines(
        db,
        [
            rec("user", "u1", "connection refused on port 80"),
            rec(
                "assistant",
                "a1",
                [
                    {"type": "text", "text": "refused connection means nothing listens"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "curl localhost refused"}},
                ],
            ),
            rec("user", "u2", [{"type": "tool_result", "content": "curl: connection refused"}]),
            rec("user", "u3", "other session about refused", sid="s2"),
        ],
        project="p",
    )
    return db


def test_phrase_search_needs_the_words_in_order():
    db = make()
    assert {h.id for h in search(db, '"connection refused"')} == {"u1:0", "u2:0"}
    assert fts_query('"two words" x*') == '"two words" "x"*'
    assert search(db, '"') == []


def test_filters():
    db = make()
    assert {h.id for h in search(db, "refused", role="assistant")} == {"a1:0", "a1:1"}
    assert {h.id for h in search(db, "refused", kind="tool_result")} == {"u2:0"}
    assert {h.id for h in search(db, "refused", tool="Bash")} == {"a1:1"}
    assert {h.session_id for h in search(db, "refused", project="p")} == {"s1", "s2"}


def test_status_and_forget():
    db = make()
    assert "2 sessions, 5 turns" in status(db)
    assert forget(db, session="s2") == 1
    assert search(db, "other") == []
    assert forget(db, project="p") == 4 and "0 sessions" in status(db)
    assert db.execute("SELECT count(*) FROM blob").fetchone()[0] == 0
    db.execute("INSERT INTO turn_fts(turn_fts) VALUES ('integrity-check')")


def test_forget_needs_a_target():
    import pytest

    with pytest.raises(ValueError):
        forget(make())


def test_forget_all_removes_big_bodies_too():
    db = connect(":memory:")
    ingest_lines(db, [rec("user", "big", "needle " + "x " * 6000)], project="p")
    assert db.execute("SELECT count(*) FROM blob").fetchone()[0] == 1
    forget(db, everything=True)
    assert db.execute("SELECT count(*) FROM blob").fetchone()[0] == 0


def test_doctor_and_setup_text(tmp_path, capsys):
    from jev_context.__main__ import main
    from jev_context.maintain import doctor, mcp_command

    db = make()
    lines = doctor(db, port=1)  # nothing listens on port 1
    assert lines[1].startswith("OK search works") and lines[2].startswith("WARN local server")
    assert doctor(connect(":memory:"), port=1)[1].startswith("FAIL log is empty")
    cmd = mcp_command(tmp_path / "log.sqlite")
    assert cmd.startswith("claude mcp add jev-context -- ") and "jev_context.mcp_server --db" in cmd
    root = tmp_path / "t" / "p"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(
        rec("user", "u9", "a reasonably long sentence about zebra crossings") + "\n", encoding="utf-8"
    )
    main(["--db", str(tmp_path / "log.sqlite"), "setup", "--root", str(tmp_path / "t")])
    out = capsys.readouterr().out
    assert "OK search works" in out and "claude mcp add" in out
