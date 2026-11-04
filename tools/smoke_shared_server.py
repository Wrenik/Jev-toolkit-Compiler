"""Run: python tools/smoke_shared_server.py (needs both packages installed). End-to-end smoke of the SPLIT packages in a fresh venv: shared server (router + context in one judge call)
and the MCP server over stdio with the new data location. Fake judge, tmp JEV_HOME, port 18765."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="jevhome-"))
env = {**os.environ, "JEV_HOME": str(home), "JEV_JUDGE": "fake"}
py = sys.executable
PORT = 18765

# 1. MCP over stdio, db from JEV_HOME (no --db, no cwd)
root = home / "t" / "proj"
root.mkdir(parents=True)
(root / "s.jsonl").write_text(json.dumps({"type": "user", "uuid": "u1", "sessionId": "s", "timestamp": "2026-10-01T10:00:00Z",
                                           "message": {"content": "we chose the walrus cache design"}}) + "\n", encoding="utf-8")
out = subprocess.run([py, "-m", "jev_context", "ingest", "--root", str(home / "t")], env=env, capture_output=True, text=True, cwd=tempfile.gettempdir())
print(out.stdout.strip().splitlines()[-1])
req = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
       {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_log", "arguments": {"query": "walrus"}}}]
res = subprocess.run([py, "-m", "jev_context.mcp_server"], env=env, input="\n".join(map(json.dumps, req)) + "\n",
                     capture_output=True, text=True, cwd=tempfile.gettempdir())
answers = [json.loads(x) for x in res.stdout.splitlines()]
assert "showing 1 of 1 matches" in answers[1]["result"]["content"][0]["text"], res.stdout + res.stderr
print("MCP search via JEV_HOME: OK")
assert (home / "var" / "log" / "jev-context.sqlite").exists()

# 2. shared server: router + context requests at once -> one judge call
srv = subprocess.Popen([py, "-m", "jev_context", "serve", "--port", str(PORT)], env=env, cwd=tempfile.gettempdir(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers={"X-Jev-Router": "1"}), timeout=1)
            break
        except OSError:
            time.sleep(0.2)

    def post(path, body):
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                   headers={"X-Jev-Router": "1", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r, timeout=15).read())

    # make the broker know both partners exist (one request each, sequentially), then fire both at once
    post("/route", {"prompt": "hello"})
    post("/advise", {"recent_prompts": ["x"], "context_tokens": 300000})
    results = {}
    barrier = threading.Barrier(2)

    def go(name, path, body):
        barrier.wait()
        results[name] = post(path, body)

    ts = [threading.Thread(target=go, args=("route", "/route", {"prompt": "rename a variable"})),
          threading.Thread(target=go, args=("advise", "/advise", {"recent_prompts": ["keep going"], "context_tokens": 300000}))]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    h = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers={"X-Jev-Router": "1"}), timeout=5).read())
    print("route ok:", results["route"]["ok"], "advise ok:", results["advise"]["ok"], "joint_calls:", h["joint_calls"], "solo_calls:", h["solo_calls"], "has_context:", h["has_context"])
    assert results["route"]["ok"] and results["advise"]["ok"] and h["joint_calls"] >= 1
    assert (home / "var" / "router-log.jsonl").exists() and (home / "var" / "context-advice.jsonl").exists()
    print("files in JEV_HOME/var:", sorted(p.name for p in (home / "var").iterdir()))
finally:
    srv.terminate()
print("SMOKE OK")
