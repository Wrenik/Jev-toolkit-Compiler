from __future__ import annotations

from jev_context import window as w
from jev_context.install import install, write_env


def _conv(n: int, huge: bool = False) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"prompt {i} about topic{i}"})
        msgs.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"answer {i}"},
                    {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": f"f{i}.py"}},
                ],
            }
        )
        msgs.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": ("x" * 30000) if huge else "ok"}
                ],
            }
        )
        msgs.append({"role": "assistant", "content": f"done {i}"})
    return msgs


def test_short_conversation_is_untouched() -> None:
    m = _conv(1)
    assert w.History().reduce(m) == m


def test_reduce_keeps_newest_prompts_and_adds_outline() -> None:
    m = _conv(8)
    out = w.History().reduce(m)
    assert out[0]["role"] == "user" and "OUTLINE" in out[0]["content"] and "prompt 0 about topic0" in out[0]["content"]
    assert out[1]["role"] == "assistant"
    assert out[2]["content"] == "prompt 6 about topic6"
    assert out[-1] == m[-1]
    assert [x["role"] for x in out[:4]] == ["user", "assistant", "user", "assistant"]


def test_tool_pairing_survives_and_old_results_are_cut() -> None:
    m = _conv(8, huge=True)
    out = w.History().reduce(m)
    ids_use = [
        b["id"] for x in out if isinstance(x["content"], list) for b in x["content"] if b.get("type") == "tool_use"
    ]
    ids_res = [
        b["tool_use_id"]
        for x in out
        if isinstance(x["content"], list)
        for b in x["content"]
        if b.get("type") == "tool_result"
    ]
    assert ids_use == ids_res
    assert w.size_of(out) < w.size_of(m) / 3
    assert any(
        "get_history" in str(b.get("content")) for x in out if isinstance(x["content"], list) for b in x["content"]
    )


def test_dropped_text_can_be_found_again() -> None:
    h = w.History()
    h.reduce(_conv(8))
    hit = h.call("search_history", {"query": "topic2"})
    assert "prompt 2 about topic2" in hit and "DATA" in hit
    first_id = h.search("topic2")[0]["id"]
    assert "topic2" in h.call("get_history", {"id": first_id})
    assert h.call("get_history", {"id": "nope"}) == "No such id."


def test_history_survives_a_restart(tmp_path) -> None:
    h = w.History("t", folder=tmp_path)
    h.reduce(_conv(8))
    again = w.History("t", folder=tmp_path)
    assert again.search("topic3")


def test_env_never_prints_the_key_and_keeps_existing_lines(tmp_path, capsys) -> None:
    lines: list[str] = []
    write_env(tmp_path, "SECRET123", judge_off=False, say=lines.append, dry=False)
    assert "SECRET123" in (tmp_path / ".env").read_text()
    assert not any("SECRET123" in x for x in lines)
    write_env(tmp_path, "OTHER", judge_off=False, say=lines.append, dry=False)
    assert (tmp_path / ".env").read_text().count("TYPESAFE_API_KEY") == 1


def test_no_jev_writes_the_offline_judge(tmp_path) -> None:
    write_env(tmp_path, None, judge_off=True, say=lambda s: None, dry=False)
    assert "JEV_JUDGE=fake" in (tmp_path / ".env").read_text()


def test_dry_run_changes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    out: list[str] = []
    assert install(where="api", jev="no", assume_yes=True, dry_run=True, say=out.append) == 0
    assert not (tmp_path / ".env").exists()
    assert any("Use it with any Anthropic-style API" in x for x in out)
