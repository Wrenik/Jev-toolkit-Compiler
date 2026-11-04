import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from jev_core import FakeJudge

from jev_context.autoingest import AutoIngest
from jev_context.broker import Broker
from jev_context.search import search
from jev_context.server import make_handler
from jev_context.store import connect


def write_transcript(root, name, text, uuid):
    folder = root / "proj"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": uuid,
                "sessionId": name,
                "timestamp": "2026-10-01T10:00:00Z",
                "message": {"content": text},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "transcripts"
    write_transcript(root, "s1", "the walrus cache decision", "u1")
    return root, tmp_path / "log.sqlite"


def test_background_ingest_finds_new_transcripts_and_is_idempotent(setup):
    root, db_path = setup
    auto = AutoIngest(db_path, root, min_interval_s=0)
    assert auto.request()["started"] is True
    assert wait_for(lambda: auto.runs == 1 and auto.last_ok_at)
    assert [h.id for h in search(connect(db_path), "walrus")] == ["u1:0"]
    write_transcript(root, "s2", "a second quokka note", "u2")
    assert auto.request()["started"] and wait_for(lambda: auto.runs == 2)
    assert auto.request(wait=True)["stats"]["added"] == 0  # nothing new: idempotent
    db = connect(db_path)
    assert db.execute("SELECT count(*) FROM turn").fetchone()[0] == 2


def test_rate_limit_and_force(setup):
    root, db_path = setup
    now = [100.0]
    auto = AutoIngest(db_path, root, min_interval_s=180, clock=lambda: now[0])
    assert auto.request(wait=True)["ok"]
    assert auto.request() == {"ok": True, "started": False, "reason": "rate-limited"}
    now[0] += 179
    assert auto.request()["started"] is False
    assert auto.request(force=True, wait=True)["ok"]  # manual ingest ignores the limit
    now[0] += 400
    assert auto.request()["started"] is True
    assert wait_for(lambda: auto.runs == 3)


def test_a_failing_ingest_is_recorded_not_raised(tmp_path, capsys):
    root = tmp_path / "t"
    root.mkdir()
    bad_db = tmp_path / "adir"
    bad_db.mkdir()  # a directory cannot be opened as a database
    auto = AutoIngest(bad_db, root, min_interval_s=0)
    assert auto.request()["started"] is True  # the request itself never fails
    assert wait_for(lambda: auto.runs == 1)
    assert auto.last_error and "ingest failed" in capsys.readouterr().err
    assert auto.request(wait=True)["ok"] is False  # the manual path reports it instead


def test_overlapping_requests_do_not_run_twice(setup, monkeypatch):
    root, db_path = setup
    gate = threading.Event()
    import jev_context.autoingest as mod

    real = mod.ingest_tree
    monkeypatch.setattr(mod, "ingest_tree", lambda db, r: (gate.wait(5), real(db, r))[1])
    auto = AutoIngest(db_path, root, min_interval_s=0)
    assert auto.request()["started"] is True
    assert auto.request() == {"ok": True, "started": False, "reason": "already running"}
    assert auto.request(wait=True)["ok"] is False  # manual: told to retry
    gate.set()
    assert wait_for(lambda: auto.runs == 1)


# --- through the HTTP handler -----------------------------------------------------------------


def post(httpd, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_address[1]}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"X-Jev-Router": "1"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def health(httpd):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/health", headers={"X-Jev-Router": "1"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def test_http_auto_returns_at_once_and_manual_returns_stats(setup, tmp_path):
    root, db_path = setup
    handler = make_handler(Broker(FakeJudge()), tmp_path / "r.jsonl", tmp_path / "a.jsonl", db_path, root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        first = post(httpd, "/ingest", {"auto": True})
        assert first["ok"] and first["started"] is True and "stats" not in first  # did not wait for the work
        assert wait_for(lambda: health(httpd)["ingest"]["runs"] == 1)
        again = post(httpd, "/ingest", {"auto": True})
        assert again["started"] is False and again["reason"] == "rate-limited"  # default 180 s window
        manual = post(httpd, "/ingest", {})
        assert manual["ok"] and manual["stats"]["files"] == 1
        h = health(httpd)["ingest"]
        assert h["last_error"] is None and h["last_ok_at"]
    finally:
        httpd.shutdown()
