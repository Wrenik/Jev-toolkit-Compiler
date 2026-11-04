# Contributing

Thanks for looking. This is a small learning project, so the bar is "clear and honest", not "enterprise".

1. `python -m venv .venv`; install the base repo first (`pip install -e ../Jev-toolkit-usage`, a clone of [Jev-toolkit-usage](https://github.com/Wrenik/Jev-toolkit-usage) next to this one), then `pip install --no-deps -e ".[dev]"` (`--no-deps` until Jev-toolkit-usage is public; pip would otherwise try to clone it) (`.venv/Scripts/python -m pip ...` on Windows).
2. Run the checks: `python -m ruff check src tests tools`, `python -m mypy` and the tests, `python -m pytest`. They are offline and need no API key (`JEV_JUDGE=fake` is set in CI). Tests marked `live` call the real Jev API and are skipped by default.
3. New behaviour comes with a test. A bug fix comes with a regression test that fails without the fix.
4. Keep documentation claims measured: say what was tested, on how many cases, and what the limits are.
5. Never use the Jev SDK directly outside `src/jev_core/jev.py`; tools talk to the `Judge` interface so the judge can be replaced by a local or European model.

Good first issues: a secret format the log's redaction misses (with an inert example), a search that should have found something, documentation that is unclear.

Style and types: `python -m ruff format . && python -m ruff check . && python -m mypy` must pass (CI runs the same checks on Linux, Windows and macOS, Python 3.11 and 3.13). Install the tools with `pip install -e ".[dev]"`.
