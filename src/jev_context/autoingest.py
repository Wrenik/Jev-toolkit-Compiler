"""Background ingest for the local server: cheap, rate-limited, never blocks a prompt, failures only logged.

The mod asks for an ingest when a session starts and ends; the server also runs one when it starts. Ingest is
idempotent (rows are keyed by record uuid and unchanged files are skipped by size + mtime), so asking often is
safe; the rate limit keeps several sessions from re-scanning the transcript folder every few seconds.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .ingest import ingest_tree
from .store import connect

DEFAULT_MIN_INTERVAL_S = 180.0


class AutoIngest:
    def __init__(
        self,
        db_path: Path,
        root: Path,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db_path, self.root, self.min_interval_s, self._clock = Path(db_path), Path(root), min_interval_s, clock
        self._busy = threading.Lock()
        self._last_started: float | None = None
        self.runs = 0
        self.last_ok_at: float | None = None  # wall clock
        self.last_error: str | None = None
        self.last_stats: dict[str, int] | None = None

    def _work(self) -> dict:
        try:
            db = connect(self.db_path)
            try:
                self.last_stats = ingest_tree(db, self.root)
            finally:
                db.close()
            self.last_ok_at, self.last_error = time.time(), None
            return {"ok": True, "stats": self.last_stats}
        except Exception as err:  # noqa: BLE001  a failing ingest must never take the server down
            self.last_error = f"{type(err).__name__}: {err}"
            print(f"jev_context: ingest failed: {self.last_error}", file=sys.stderr, flush=True)
            return {"ok": False, "error": self.last_error}
        finally:
            self.runs += 1
            self._busy.release()

    def request(self, force: bool = False, wait: bool = False) -> dict:
        """Start an ingest unless one ran too recently (`force` skips that rule) or one is running.

        wait=False returns at once with {"ok": True, "started": bool, "reason": ...}; the work runs in a
        daemon thread. wait=True runs it here and returns its result (manual `/jevctx ingest`)."""
        now = self._clock()
        if not force and self._last_started is not None and now - self._last_started < self.min_interval_s:
            return {"ok": True, "started": False, "reason": "rate-limited"}
        if not self._busy.acquire(blocking=False):
            return (
                {"ok": True, "started": False, "reason": "already running"}
                if not wait
                else {"ok": False, "error": "an ingest is already running; try again in a moment"}
            )
        self._last_started = now
        if wait:
            return self._work()
        threading.Thread(target=self._work, daemon=True, name="jev-context-ingest").start()
        return {"ok": True, "started": True}

    def status(self) -> dict:
        return {
            "runs": self.runs,
            "running": self._busy.locked(),
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
        }
