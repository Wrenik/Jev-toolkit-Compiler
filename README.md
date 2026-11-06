# Jev-toolkit-Compiler

[![tests](https://github.com/Wrenik/Jev-toolkit-Compiler/actions/workflows/tests.yml/badge.svg)](https://github.com/Wrenik/Jev-toolkit-Compiler/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![status](https://img.shields.io/badge/status-alpha-orange)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A context log and compact/clear advisor for **Claude Code**, built on a judgement model ([TypeSafe Jev](https://docs.typesafe.ai)). It is part of a family of small Jev tools; the base layer (judge interface, settings, the router mod) is [Jev-toolkit-usage](https://github.com/Wrenik/Jev-toolkit-usage), which this repository depends on.

It does three things, all on a Claude **subscription** (no API tokens):

1. **Log**: reads Claude Code's transcripts into a local SQLite/FTS5 database, with secrets and e-mail addresses redacted first. Claude Code deletes transcripts after about 30 days; the log keeps them.
2. **Search**: a command line and a read-only MCP server so you (or the agent, on purpose) can look things up later. Results are framed as quoted data, never as instructions.
3. **Advise**: when the context is big, ask Jev whether the work will continue and how much of the context is still needed, then a cost model says keep, compact now, or clear. Advice only: it never runs `/compact` or `/clear`.

## Status and limits

**Built and tested offline; the mod is not verified in the Claude Code desktop app.** What is measured and what is assumed:

| Claim | Status |
|---|---|
| Tests pass (offline, synthetic data) | measured |
| Search recall@5 0.97-1.00 | measured on the author's own transcripts with an easy proxy (rare words of the turn itself); vague human questions are untested |
| Ingest speed | 1 GB of synthetic transcripts in 269 s (3.7 MB/s) on one Windows machine; a very common word takes about 0.8 s to search |
| Break-even turns, compact/clear advice | computed from **assumed** weights (read 0.1, write 2.0, output 5.0) and an assumed summary ratio; not calibrated on subscription usage |
| Joint router + context request | works against live Jev in one test; whether it happens in real use depends on hook order, **unverified** |
| Mod loads and the hook fires in Claude Code | **unverified**; `register.tsx` is not type-checked |
| MCP server config | verified in a headless Claude Code session on the author's machine |
| Phone numbers, addresses, names in prose | **not redacted** |

Full details, the cost model, measurements and prior art: [docs/CONTEXT_COMPILER.md](docs/CONTEXT_COMPILER.md).

## Install

Want all six tools? Use [the install script](https://github.com/Wrenik/Jev-toolkit-usage/blob/main/docs/INSTALL_ALL.md) (`scripts/install_all.ps1` or `.sh` in Jev-toolkit-usage) instead of the steps below.

Requires Python 3.11+ and `python` on your PATH (use `python3` on Linux/macOS; see the mod's `python` option or `JEV_PYTHON`).

```bash
pip install "git+https://github.com/Wrenik/Jev-toolkit-usage.git" "git+https://github.com/Wrenik/Jev-toolkit-Compiler.git"
# development, from local clones:
pip install -e ../Jev-toolkit-usage && pip install -e ".[dev]"
```

Use a fresh virtual environment (`python -m venv .venv`). `python -m jev_context --version` checks the install.

Put your TypeSafe key in `~/.jev-toolkit/.env` (or the folder named by `JEV_HOME`; see Jev-toolkit-usage). The log lives in `~/.jev-toolkit/var/log/jev-context.sqlite` and the advisor's decision log in `~/.jev-toolkit/var/context-advice.jsonl`.

## Quick try (offline, no key)

```bash
python -m pytest                                  # offline tests
python -m jev_context ingest --dry-run            # count what a first ingest would add; writes nothing
python -m jev_context setup                       # ingest ~/.claude/projects, check everything, print the MCP command
python -m jev_context search "some words you remember"
python -m jev_context status                      # what the log holds and where it lives
python -m jev_context stats                          # per-project sizes, dates, top tools
python -m jev_context search "cache design" --project NAME --since 2026-10-01 --role user --offset 10
python -m jev_context forget --session ID --yes   # delete one session's rows (or --project / --all)
```

What a session looks like (made-up transcript; the key and address are inert examples; hits are wrapped in « », and times are shown in your local time zone):

```
$ python -m jev_context ingest --root ./demo
this run: {'records': 3, 'added': 3, 'duplicates': 0, 'skipped': 0, 'malformed': 0, 'redactions': 2, 'files': 1}
log now: 1 sessions, 3 turns, 2 redactions, database 0.0 MB at ~/.jev-toolkit/var/log/jev-context.sqlite

$ python -m jev_context search "invoices index"
a1:0  2026-10-02 11:00  assistant/text
    The «invoices» query has no «index» on customer_id; add one and the export drops from 90 s to 2 s.

$ python -m jev_context search "mail staging"
u2:0  2026-10-02 11:05  user/text
    Use key [REDACTED:anthropic_key] for the «staging» run, mail me at [REDACTED:email]
```

Hits are marked with `[brackets]`. `search --json` prints one JSON object per line for scripts; `get ID --around 2` shows a turn with its neighbours. `skipped` counts records that are not conversation (bookkeeping), `malformed` lines that are not valid JSON objects (for example a transcript cut off mid-write), `errors` files that could not be read.

`setup` prints the exact `claude mcp add ...` line for your machine; the plugin also ships its own `.mcp.json`, so with the mod installed the search tool is registered for you.

## Install the Claude Code mod (`jev-context`)

```
/plugin marketplace add Wrenik/Jev-toolkit-Compiler
/plugin install jev-context --marketplace Wrenik/Jev-toolkit-Compiler
```

or add `claude-mod/jev-context` to `CLAUDE_CODE_PLUGIN_DIRS`. The mod starts `python -m jev_context serve` itself (no checkout path needed). `/jevctx` shows its state; `/jevctx mode off` stops the advisor; `/jevctx ingest` and `/jevctx search <words>` use the log. The mod asks only when the context is above 100,000 tokens, at most once per 5 minutes. When [Jev-toolkit-usage](https://github.com/Wrenik/Jev-toolkit-usage)'s `jev-router` mod is also installed, both share one judge request through the same local server; each also works alone.

## Automatic ingest, and why it matters

Claude Code **deletes local session transcripts after 30 days by default** (the setting is `cleanupPeriodDays`). The log keeps whatever it ingested, but only what it ingested *before* a transcript was deleted. So the mod ingests on its own, with no command to remember:

* when a session **starts** and when it **ends** (the mod asks the local server, which runs the ingest in a background thread; the prompt is never delayed),
* once when the local server starts,
* at most once per 3 minutes per session, and at most once per 3 minutes in the server (several sessions together do not re-scan the folder),
* idempotently: rows are keyed by record id, unchanged files are skipped by size and modification time, so asking often is cheap.

A failed ingest is only logged (stderr of the server, and `/jevctx` shows the last result); it never blocks or breaks a prompt. `/jevctx ingest` still ingests right now; `/jevctx autoingest off` turns the automatic part off.

If you also want the **original** transcripts to stay longer, you can raise `cleanupPeriodDays` yourself in your own `~/.claude/settings.json` (for example `{"cleanupPeriodDays": 99999}`). This repository never edits that file. Do it only if you want the raw files kept: they still contain every secret you typed or printed, unredacted.

**What the log stores:** the text of your prompts and the assistant's replies, tool calls (name and input) and tool results, with secrets, e-mail addresses and home-directory user names (`C:\Users\<name>`, `/home/<name>`) replaced first. It does **not** store thinking blocks or the API-request copies. It does not detect phone numbers, postal addresses or names written in ordinary sentences. Redaction is best effort and runs after the transcript was written; see section 9 of [docs/CONTEXT_COMPILER.md](docs/CONTEXT_COMPILER.md).

## Verify it in the desktop app (5 minutes)

The mod has never been confirmed to work inside the Claude Code desktop app by anyone but the author's tests, so please check, and report what you see (issue or message) if a step differs.

1. **Load.** Install as above (or `CLAUDE_CODE_PLUGIN_DIRS`), `python` on PATH with both packages installed, restart Claude Code, open a session. Expect: no error; `/jevctx` is a known command.
2. **Status.** Run `/jevctx`. Expect lines for `mode: advise`, `server: up at http://127.0.0.1:8765, context endpoints yes`, an `automatic ingest` line, and `last advice: no advice yet`. If the server is `down`, wait a few seconds and run it again; if it stays down, the mod could not start `python -m jev_context serve` (check that `python -m jev_context --help` works in a normal terminal, or set the mod's `python` option / `JEV_PYTHON`).
3. **Log and search.** After a few seconds `/jevctx` should show a server ingest with `runs` of at least 1 and `last_error: null`. Then `/jevctx search <a distinctive word from an older session>`. Expect lines like `<id> [user/text] "...[word]..."`. No hits for a word you know is there: run `/jevctx ingest` once and retry; report if it still fails.
4. **Advice toast.** Advice only appears above 100,000 tokens of context, at most once per 5 minutes, and `keep` is silent. Work in one session until the context is past that (or use a long existing session), send a normal prompt, and wait a few seconds. Expect either nothing (keep) or a toast starting `Jev suggests /compact` or `Jev suggests /clear`, with a reason that contains numbers. `/jevctx` then shows `last advice: ...` either way. If it stays `no advice yet` above 100k tokens, report your `last prompt seen:` line.
5. **Agent look-back.** Make sure the MCP tool is registered (`/mcp` should list an entry containing `jev-context`; otherwise run the `claude mcp add` line printed by `python -m jev_context setup`). Ask the agent something that needs old context, for example: "Search the past sessions log: what did we decide about <a topic from an earlier session>?" Expect the agent to call the `search_log` tool (you see the tool call) and answer from quoted results, which are wrapped in a `<quoted-past-sessions id=...>` block. If it answers from memory without calling the tool, say so in a report together with your exact wording: the tool's description tells the model to use it for look-back, but whether it does so unprompted is exactly what this step tests.

## Privacy

* Ingest, search, `forget`, `tier` and the MCP server are local SQLite operations; the MCP server makes no network calls.
* Only the **advisor** talks to TypeSafe (Jev): the last up to 5 prompts of the current session (secrets redacted first) plus the question set, when the context is big. **Jev is used because, as of today, it is the only model of its kind on the market, not because sending data to a US company is the goal. The plan is to replace it with a local model or a European provider** through the `Judge` interface in `jev_core`.
* Redaction is best effort and runs after Claude Code wrote the transcript, so the raw transcripts in `~/.claude/projects` still hold every secret. The log holds a second, redacted copy. Treat the database as personal data. Details: section 9 of the document.
* Delete everything: `python -m jev_context forget --all --yes`, or delete `~/.jev-toolkit/var/log/`.

## Prior art

Several tools already index Claude Code transcripts or add memory: [claude-mem](https://github.com/thedotmack/claude-mem), [claude-session-search](https://pypi.org/project/claude-session-search/), Memex, tim0120's history MCP, and compressors such as sqz. This repository is a re-implementation of known ideas for learning, with redaction at ingest, retention tiers and a cost-based keep/compact/clear decision made by a judgement model as its differences. See the survey and comparison in [docs/CONTEXT_COMPILER.md](docs/CONTEXT_COMPILER.md) (sections 2 and 10). No code from those projects is used.

## The Jev toolkit family

| Repository | What it is | Depends on |
|---|---|---|
| [Jev-toolkit-usage](https://github.com/Wrenik/Jev-toolkit-usage) | `jev_core` (judge interface, Jev adapter, cache, decision log, redaction, settings and data location) and the model/effort router | - |
| **Jev-toolkit-Compiler** (this one) | context log, search and a compact/clear advisor | usage |
| [Jev-toolkit-Scanner](https://github.com/Wrenik/Jev-toolkit-Scanner) | prompt-injection scanner (experimental) | usage |
| [Jev-toolkit-Monitor](https://github.com/Wrenik/Jev-toolkit-Monitor) | agent monitor (experimental) | usage |
| [Jev-toolkit-FactCheck](https://github.com/Wrenik/Jev-toolkit-FactCheck) | fact checker with hover evidence | usage (Scanner optional) |
| [Jev-toolkit-Sources](https://github.com/Wrenik/Jev-toolkit-Sources) | source restriction (allowlist policy) | usage, FactCheck |

All six on one machine: [docs/INSTALL_ALL.md](https://github.com/Wrenik/Jev-toolkit-usage/blob/main/docs/INSTALL_ALL.md) (a script does it). The other tools (Scanner, Monitor, FactCheck, Sources) are published one by one; their links work as each goes public.

## Contributing, security, license

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Issues and suggestions are welcome, especially where an existing tool already does it better.

Licensed under the [MIT License](LICENSE).
