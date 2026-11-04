"""Edge cases found in review: odd JSON shapes, sub-agent projects, console encoding, odd MCP params."""

import io
import json
import sqlite3

from jev_context import __main__ as cli
from jev_context.ingest import ingest_lines, ingest_tree, project_label
from jev_context.mcp_server import handle, serve
from jev_context.search import search
from jev_context.store import connect


def rec(uuid, content, sid="s1"):
    return json.dumps({"type": "user", "sessionId": sid, "uuid": uuid, "message": {"content": content}})


def test_odd_json_shapes_do_not_crash_ingest():
    lines = ["[1,2,3]", "42", '"str"', "null", "{", "", "   ",
             json.dumps({"type": "user", "sessionId": "s", "uuid": "a", "message": "not a dict"}),
             json.dumps({"type": "user", "sessionId": 5, "uuid": "b", "message": {"content": "x"}}),
             json.dumps({"type": "assistant", "sessionId": "s", "uuid": "c", "message": {"content": [1, None, {"type": "text", "text": "ok zebra"}]}}),
             "[" * 100_000]
    db = connect(":memory:")
    stats = ingest_lines(db, lines)
    assert stats["added"] == 1 and stats["malformed"] >= 5
    assert search(db, "zebra")


def test_subagent_files_belong_to_the_parent_project(tmp_path):
    root = tmp_path / "projects"
    (root / "C--Users-Bob-work" / "sess" / "subagents").mkdir(parents=True)
    f = root / "C--Users-Bob-work" / "sess" / "subagents" / "agent-1.jsonl"
    f.write_text(rec("u1", "hello giraffe") + "\n", encoding="utf-8")
    db = connect(":memory:")
    assert ingest_tree(db, root)["added"] == 1
    assert db.execute("SELECT project FROM session").fetchone()[0] == "C--Users-USER-work"
    assert project_label(root, root / "p" / "x.jsonl") == "p"


def test_unreadable_file_is_counted_not_fatal(tmp_path, monkeypatch):
    (tmp_path / "a.jsonl").write_text(rec("u1", "alpha") + "\n", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(rec("u2", "beta", sid="s2") + "\n", encoding="utf-8")
    real = type(tmp_path / "a.jsonl").open

    def flaky(self, *a, **k):
        if self.name == "a.jsonl":
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(type(tmp_path), "open", flaky)
    db = connect(":memory:")
    stats = ingest_tree(db, tmp_path)
    assert stats["errors"] == 1 and stats["added"] == 1


def test_file_db_uses_wal(tmp_path):
    db = connect(tmp_path / "x.sqlite")
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_second_writer_waits_instead_of_failing(tmp_path):
    a, b = connect(tmp_path / "x.sqlite"), connect(tmp_path / "x.sqlite")
    ingest_lines(a, [rec("u1", "one")])
    a.commit()
    ingest_lines(b, [rec("u2", "two")])
    b.commit()
    assert a.execute("SELECT count(*) FROM turn").fetchone()[0] == 2


def test_cli_search_survives_a_console_without_unicode(tmp_path, monkeypatch, capsys):
    dbp = tmp_path / "x.sqlite"
    db = connect(dbp)
    ingest_lines(db, [rec("u1", "emoji \U0001F600 snö zebra")])
    db.commit()
    db.close()
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(cli.sys, "stdout", buf)
    assert cli.main(["--db", str(dbp), "search", "zebra"]) == 0
    buf.flush()


def test_mcp_rejects_bad_params_without_crashing():
    for params in ([], "x", None, {"name": "search_log", "arguments": [1]}, {"name": "search_log", "arguments": {"query": "a", "days": 1e308}},
                   {"name": "search_log", "arguments": {"query": "a", "days": True}}, {"name": 5}):
        out = handle(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert out is not None and ("result" in out or "error" in out)


def test_mcp_serve_stays_up_after_garbage():
    inp = io.StringIO('{"id":1,"method":"tools/call","params":[]}\nnot json\n[1]\n{"id":2,"method":"ping"}\n')
    out = io.StringIO()
    serve(":memory:-does-not-exist", inp, out)
    assert len(out.getvalue().splitlines()) == 4
