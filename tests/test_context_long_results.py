"""A word anywhere in a big tool result must be findable and readable again (not just the head and tail)."""

from __future__ import annotations

import json

from jev_context.ingest import ingest_lines
from jev_context.mcp_server import call_tool
from jev_context.search import count_hits, get_turn, search
from jev_context.store import connect, reindex_chunks


def _file_read(n_lines: int = 1200) -> str:
    return "\n".join(
        f"{i:>6}\tline {i} of a long file about topic{i % 7}" + (" zebramarker" if i == 640 else "")
        for i in range(1, n_lines + 1)
    )


def _rec(uuid: str, role: str, content: object, ts: str = "2026-10-01T10:00:00Z") -> str:
    return json.dumps({"type": role, "sessionId": "s", "uuid": uuid, "timestamp": ts, "message": {"content": content}})


def _db_with_big_result(tmp_path=None):
    db = connect(":memory:")
    body = _file_read()
    ingest_lines(
        db,
        [
            _rec("a", "user", "read the big file"),
            _rec(
                "b", "assistant", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "big.py"}}]
            ),
            _rec("c", "user", [{"type": "tool_result", "tool_use_id": "t1", "content": body}]),
            _rec("d", "assistant", [{"type": "text", "text": "done"}]),
        ],
    )
    return db, body


def test_a_word_in_the_middle_of_a_long_result_is_found() -> None:
    db, body = _db_with_big_result()
    assert len(body) > 30_000
    hits = search(db, "zebramarker")
    assert hits and hits[0].id == "c:0"
    assert hits[0].offset is not None and 4_000 <= hits[0].offset < len(body) - 4_000
    assert count_hits(db, "zebramarker") == 1


def test_head_words_still_match_without_an_offset() -> None:
    db, _ = _db_with_big_result()
    hits = search(db, "read the big file")
    assert hits and hits[0].offset is None


def test_get_turn_pages_through_the_whole_body() -> None:
    db, body = _db_with_big_result()
    got = get_turn(db, "c:0", max_chars=10_000)[0]
    assert got["total"] == len(body) and got["next_offset"] == 10_000
    seen, off = got["text"], got["next_offset"]
    while off:
        part = get_turn(db, "c:0", max_chars=10_000, offset=off)[0]
        seen += part["text"]
        off = part["next_offset"]
    assert seen == body


def test_match_returns_the_stretch_around_the_word() -> None:
    db, _ = _db_with_big_result()
    got = get_turn(db, "c:0", match="zebramarker")[0]
    assert "line 640 of a long file" in got["text"] and "1 occurrence" in got["note"]
    assert len(got["text"]) < 3_000
    assert "no occurrence" in get_turn(db, "c:0", match="absent-word")[0]["text"]


def test_lines_returns_a_range_of_lines() -> None:
    db, _ = _db_with_big_result()
    got = get_turn(db, "c:0", lines="638-641")[0]["text"]
    assert "line 640 of" in got and "line 637 of" not in got and "line 642 of" not in got
    assert get_turn(db, "c:0", lines="x")[0]["text"].startswith("lines must look like")


def test_the_mcp_tools_expose_it(tmp_path) -> None:
    db_file = tmp_path / "log.sqlite"
    db = connect(db_file)
    body = _file_read()
    ingest_lines(db, [_rec("c", "user", [{"type": "tool_result", "tool_use_id": "t1", "content": body}])])
    db.commit()
    found = call_tool(connect(db_file), "search_log", {"query": "zebramarker"})
    assert "inside a long result" in found and "c:0" in found
    page = call_tool(connect(db_file), "get_turn", {"id": "c:0", "around": 0, "lines": "640"})
    assert "line 640 of" in page
    more = call_tool(connect(db_file), "get_turn", {"id": "c:0", "around": 0, "max_chars": 1000})
    assert "more: offset 1000" in more


def test_old_logs_get_their_chunks_by_reindexing() -> None:
    db, _ = _db_with_big_result()
    with db:
        db.execute("DELETE FROM chunk")
    assert search(db, "zebramarker") == []
    assert reindex_chunks(db) == 1
    assert search(db, "zebramarker")
    assert reindex_chunks(db) == 0
