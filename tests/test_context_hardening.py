import json
import os
import sqlite3

import pytest
from jev_router.redact import redact, redact_personal

from jev_context.ingest import ingest_file
from jev_context.store import connect

# Fake secrets are assembled from parts so secret scanners do not flag this file.
STRIPE = "sk_" + "live_" + "a1B2c3D4e5F6g7H8i9J0"
WEBHOOK = "whsec_" + "a1B2c3D4e5F6g7H8i9J0"
GOOGLE = "AI" + "za" + "SyA-" + "x" * 33


@pytest.mark.parametrize(
    "text,kind",
    [
        (STRIPE, "stripe_key"),
        (WEBHOOK, "stripe_key"),
        (GOOGLE, "google_key"),
        ("Cookie: sessionid=abc123; csrftoken=zzz999", "cookie"),
        ("Set-Cookie: sid=abc123; Path=/; HttpOnly", "cookie"),
    ],
)
def test_new_secret_formats_are_redacted(text, kind):
    out, found = redact(text)
    assert kind in found and "abc123" not in out and "a1B2c3" not in out and "SyA-" not in out
    assert out.lower().startswith(("[redacted", "cookie:", "set-cookie:"))


def test_personal_data_is_redacted_for_the_log_only():
    text = r"mail jane.doe@example.org, file C:\Users\jane\src\x.py and /home/jane/.ssh and /Users/jane/y"
    out, found = redact_personal(text)
    assert "jane" not in out and set(found) == {"email", "home_path"} and found.count("home_path") == 3
    assert "jane" in redact(text)[0]  # the router keeps names and paths


def test_phone_numbers_are_not_detected():
    assert redact_personal("call +46 70 123 45 67")[0] == "call +46 70 123 45 67"


def line(uuid, content):
    return (
        json.dumps(
            {
                "type": "user",
                "uuid": uuid,
                "sessionId": "s",
                "timestamp": "2026-10-01T10:00:00Z",
                "message": {"content": content},
            }
        )
        + "\n"
    )


def test_rewritten_file_with_same_size_is_reingested(tmp_path):
    f = tmp_path / "proj" / "s.jsonl"
    f.parent.mkdir()
    db = connect(":memory:")
    f.write_text(line("u-aaaaa", "aaaaa"), encoding="utf-8")
    assert ingest_file(db, f)["added"] == 1
    assert ingest_file(db, f).get("unchanged") == 1
    f.write_text(line("u-bbbbb", "bbbbb"), encoding="utf-8")  # identical size
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert ingest_file(db, f)["added"] == 1


def test_old_log_without_mtime_column_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE file_state (path TEXT PRIMARY KEY, size INTEGER NOT NULL)")
    raw.commit()
    raw.close()
    db = connect(path)
    assert any(r[1] == "mtime_ns" for r in db.execute("PRAGMA table_info(file_state)"))


def test_first_run_stats_and_dry_run(tmp_path, capsys):
    from jev_context.__main__ import main

    root = tmp_path / "t" / "proj"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(line("u1", "hello"), encoding="utf-8")
    db = tmp_path / "log.sqlite"
    assert main(["--db", str(db), "ingest", "--root", str(tmp_path / "t"), "--dry-run"]) == 0
    assert not db.exists() and "dry run" in capsys.readouterr().out
    main(["--db", str(db), "ingest", "--root", str(tmp_path / "t")])
    assert "1 sessions, 1 turns, 0 redactions" in capsys.readouterr().out


def test_gated_redaction_equals_ungated():
    import random

    rng = random.Random(1)
    bits = [
        STRIPE,
        GOOGLE,
        "Cookie: a=b",
        "api_key=abcdefgh1234",
        "password: hunter2xx",
        "https://u:pw@host/x",
        "eyJabcdefghi.abcdefghij.abcdefghij",
        "Bearer " + "A" * 24,
        "AKIA" + "B" * 16,
        "xoxb-" + "1" * 12,
        "ghp_" + "c" * 22,
        "sk-" + "d" * 24,
        "TOKEN=zzzzzzzz",
        "plain words here",
        "def f(x): return x",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ]
    for _ in range(300):
        text = " ".join(rng.choice(bits) for _ in range(rng.randrange(1, 6)))
        text = text.upper() if rng.random() < 0.2 else text
        assert redact(text, gated=True) == redact(text, gated=False)
