"""Synthetic benchmark for jev_context: generate fake transcripts, ingest, time searches.

Usage: python tools/bench_context.py WORKDIR [--mb 1000]
Everything is random words; nothing here comes from a real transcript.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jev_context.ingest import ingest_tree  # noqa: E402
from jev_context.search import search  # noqa: E402
from jev_context.store import connect  # noqa: E402

rng = random.Random(7)
VOCAB = [f"w{i}x{rng.randrange(10**4)}" for i in range(20_000)]
COMMON = ["the", "function", "error", "file", "test", "return", "value", "class", "import", "run"]


def words(n: int) -> str:
    return " ".join(rng.choice(COMMON) if rng.random() < 0.5 else rng.choice(VOCAB) for _ in range(n))


def generate(root: Path, target_mb: int) -> int:
    written, n = 0, 0
    sid_no = 0
    while written < target_mb * 1_000_000:
        sid_no += 1
        sid = f"sess-{sid_no}"
        proj = root / f"proj{sid_no % 12}"
        proj.mkdir(parents=True, exist_ok=True)
        lines = []
        for _ in range(rng.randrange(300, 900)):
            n += 1
            ts = f"2026-{rng.randrange(1, 11):02d}-{rng.randrange(1, 28):02d}T10:00:00Z"
            kind = rng.random()
            if kind < 0.25:
                rec = {"type": "user", "message": {"content": words(rng.randrange(10, 80))}}
            elif kind < 0.5:
                rec = {
                    "type": "assistant",
                    "message": {"model": "m", "content": [{"type": "text", "text": words(rng.randrange(40, 300))}]},
                }
            elif kind < 0.65:
                rec = {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": words(8)}}]},
                }
            else:
                rec = {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": words(rng.randrange(100, 3000))}]},
                }
            rec.update(uuid=f"u{n}", sessionId=sid, timestamp=ts)
            lines.append(json.dumps(rec))
        data = "\n".join(lines) + "\n"
        (proj / f"{sid}.jsonl").write_text(data, encoding="utf-8")
        written += len(data)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--mb", type=int, default=1000)
    a = ap.parse_args()
    work = Path(a.workdir)
    root = work / "transcripts"
    t = time.perf_counter()
    reuse = root.exists() and any(root.rglob("*.jsonl"))  # same data again for before/after comparisons
    size = sum(f.stat().st_size for f in root.rglob("*.jsonl")) if reuse else generate(root, a.mb)
    print(f"{'reused' if reuse else 'generated'} {size / 1e6:.0f} MB in {time.perf_counter() - t:.0f} s")
    db_path = work / "bench.sqlite"
    for suffix in ("", "-journal", "-wal"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    db = connect(db_path)
    t = time.perf_counter()
    stats = ingest_tree(db, root)
    ingest_s = time.perf_counter() - t
    db.commit()
    print(f"ingest: {ingest_s:.0f} s ({size / 1e6 / ingest_s:.1f} MB/s), {stats}")
    t = time.perf_counter()
    again = ingest_tree(db, root)
    print(f"second ingest (unchanged): {time.perf_counter() - t:.2f} s, unchanged files {again.get('unchanged')}")
    print(f"database {db_path.stat().st_size / 1e6:.0f} MB ({db_path.stat().st_size / size:.2f} x the transcripts)")
    queries = [
        ("rare", [rng.choice(VOCAB) for _ in range(40)]),
        ("two rare words", [f"{rng.choice(VOCAB)} {rng.choice(VOCAB)}" for _ in range(40)]),
        ("common word", ["function", "error test", "return value", "import class"] * 5),
    ]
    for name, qs in queries:
        times = []
        for q in qs:
            t = time.perf_counter()
            search(db, q, limit=10)
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        print(
            f"search {name}: median {statistics.median(times):.1f} ms, p95 {times[int(len(times) * 0.95) - 1]:.1f} ms, max {times[-1]:.1f} ms"
        )
    print(f"machine: {platform.platform()}, {os.cpu_count()} logical CPUs, Python {platform.python_version()}")


if __name__ == "__main__":
    main()
