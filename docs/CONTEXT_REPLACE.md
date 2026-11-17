# Context replace: keep a long memory without reading it all every turn

Status: **experimental, off by default**. Tested headless on throwaway sessions and measured (sizes only) on four of the
author's real long sessions. Not yet tested on a long real working day, and the effect on answer quality is not measured.

## Why this exists

A long Claude Code session has two ways to forget:

1. **Compaction writes a summary.** A model reads the whole conversation and writes a few paragraphs. Whatever the
   summary leaves out is gone, and the summarising call itself reads everything once more.
2. **Everything else is paid for every turn.** Until the conversation is compacted, the model re-reads old tool output
   (file listings, test logs, search results) on each turn, although most of it will never matter again.

The idea: **do not let the model carry the history. Let cheap tools carry it, and hand the model only what the current
task needs.** The history lives in a local log (nothing leaves the computer); the model gets a short outline of what
exists and can look up any detail on demand.

## What it does

When Claude Code is about to compact, the mod answers instead of the summariser. The conversation becomes:

1. **An outline, built by plain code, no model**: every earlier prompt in short, the end of each answer, the files that
   were touched. About 1-5 thousand characters however long the session was.
2. **Excerpts** the local log returns for the current task (keyword search over everything ever said, secrets redacted).
3. **The newest two prompts and what followed, word for word**, with *old tool output cut to a stub* once that tail
   is over 40 000 characters. Prompts and answers are never cut.

Everything removed stays in the log. The outline tells the model that, and gives it two tools: `search_log` to find a
detail and `get_turn` to read one turn in full. Quoted history is always framed as data, never as instructions.

## Where the cheap tools are

| Job | Done by | Cost |
|---|---|---|
| Outline, tail cutting, picking what to keep | plain code | none |
| Finding old details | SQLite full-text search, on this computer | none |
| Deciding *when* it is worth compacting, how complex the task is | optional: a small judgement model (TypeSafe **Jev**) | one cheap call, shared with the other Jev tools |
| Understanding and answering | the main model (Claude) | only what is left in the context |

**Jev is optional.** Without it (`JEV_JUDGE=fake`, or never set a key) the replacement itself still works, because it is
all local logic. Jev only adds judgement on top (for example the compact/clear advice).

## What was measured (sizes in characters; about 4 characters is one token)

Real long sessions cut at several points, **without** any excerpts (so these are the outline plus the tail):

| Session | Full | Replaced, no tail budget | Replaced, with tail budget |
|---|---|---|---|
| 3 889 messages, at 50 % | 2.21 M | 178 k | 81 k |
| 1 025 messages, at 25 % (one long agent run) | 421 k | 422 k (no gain) | 92 k |
| 806 messages, at 50 % (one long agent run) | 556 k | 452 k | 126 k |
| 806 messages, at 100 % | 1.07 M | 112 k | 43 k |

What this says, and does not say:

* The outline is small and constant. **The tail is the cost**, and it is dominated by old tool output. Without the
  tail budget the worst cases saved nothing; with it they dropped by three quarters.
* **Compared with the engine's own compaction the result is not smaller (confirmed by the long test below: 48.5 k against 48.6 k tokens).** Seven real compaction summaries in the
  author's transcripts were 2.5 k to 17 k characters, because the engine keeps *only* the summary. This mod keeps the
  last two prompts word for word, so it often ends up larger right after compaction. What it saves instead is the
  summarising call, and it keeps every detail retrievable instead of lossy.
* **Not measured:** actual subscription usage, the prompt-cache cost of the one re-read after a compaction, and whether
  the model answers better or worse afterwards. A small retrieval check (4 dropped long prompts, probed with words that the
  outline does not show) found all 4, which is far too few to claim a rate.

### A/B on a scripted session (2026-11-17, one run each)

A real Claude Code session of 7 turns (30 messages: it read six files and reported constants), forked twice. One fork was
compacted by Claude Code, the other by this mod (30 messages became 10, the summariser did not run). Then both were asked
six questions about the earlier files, with file reading switched off.

| | Claude Code's summary | This mod |
|---|---|---|
| Extra cost of the compaction itself | $0.036 | $0 (summariser skipped; the hook took 2.4 s) |
| Correct answers | 6 of 6 | 6 of 6 |

This is one run on a small session, so the cost gap in the following question turn ($0.19 against $0.10) is mostly
prompt-cache noise and **must not be read as a saving**. What it does show is that the hook works end to end in a real
session, that skipping the summariser removes its cost, and that nothing was lost on a small session. It says nothing about
a long working day.

### A/B on a longer scripted session (2026-11-17, one run each)

20 turns, 82 messages: the model read 20 different source files and reported two constants from each. Forked twice, one
fork compacted by Claude Code and one by this mod (82 messages became 8; the hook took 1.4 s, 0 excerpts, so everything
came from the outline and the model's own `search_log` calls). Then 12 questions about constants from the first, middle
and last files, with file reading switched off.

| | Claude Code's summary | This mod |
|---|---|---|
| Correct answers | 12 of 12 | 12 of 12 |
| Compaction itself | $0.054 | about $0.006 |
| The 12-question turn | $0.096 | $0.118 |
| Compaction + question, total | $0.150 | $0.124 |

Read this carefully: one run, a session whose history was only about 44 k characters of text, and a recall task that
is easy for both. The mod was cheaper in total here because the summariser did not run, but its context after compaction
is not shown here: the token figures I first printed were sums over several model calls, so they were not context sizes (the long test below measures it properly). The session was too short for the replacement to shrink anything
that matters; the result says "no loss, no big gain" and nothing about hours-long sessions.

### A/B on a long session (2026-11-17, one run each)

33 turns, 141 messages, 2.66 M characters of tool output: the model read 33 whole files (12-38 k characters each). The
uncompacted context was **320 k tokens**. Forked twice, compacted by Claude Code and by this mod (141 messages became 8,
hook 0.3 s). Then 16 questions: the line count of 8 of the files (the answer is in the model's own earlier reply) and the
exact text of one line of 8 other files (only in the tool output), with file reading switched off.

| | Claude Code's summary | This mod |
|---|---|---|
| Context after compaction, one-call turn (tokens, incl. system prompt) | 48.5 k | 48.6 k |
| Line counts right | 8 of 8 | 8 of 8 |
| Exact line text right | 0 of 8 | 0 of 8 |
| Compaction itself | $0.059 | $0 |
| The 16-question turn | $0.139 (4 model calls) | $0.219 (13 model calls) |

What it says:

* **Both compactions shrink 320 k to about 48 k, and the two results are equal in size.** Most of those 48 k is Claude
  Code's own system prompt and tools. This mod does not make the context smaller than the engine's summary does.
* **Exact text from a big tool result was lost in both.** The log keeps long tool output cut in the middle and `get_turn`
  returns at most 6 000 characters, so a line in the middle of a 30 k-character file cannot be retrieved. In real work the
  model would simply read the file again (which this test forbade), so this is a limit rather than a failure, but the log is
  **not** a faithful archive of big tool output.
* **The mod tried hard to find it anyway.** It made 13 model calls and spent $0.08 more on the question turn than the
  summary arm, which made 4 calls and gave up sooner. Net after compaction + question: $0.199 (summary) against $0.219 (mod).
* **So at this size there is no saving**, only the summariser call that is skipped, and that is eaten by the lookups when the
  question needs something the log cannot give.

## Turning it on and off

* A row above the prompt shows `Jev context: off` or `ON` with a **Turn on / Turn off** button.
* `/jevreplace on`, `off`, `toggle`, `now` (compact between turns), `status`.
* The choice is remembered between sessions. `JEV_REPLACE=1` only sets the default for people who have never chosen.
* If anything fails (server not running, short conversation, any error) the mod steps aside and Claude Code compacts as usual.
* If answers get worse: turn it off. Nothing is lost; the log keeps filling either way.

## Install

```bash
pip install "git+https://github.com/Wrenik/Jev-toolkit-usage.git" "git+https://github.com/Wrenik/Jev-toolkit-Compiler.git"
python -m jev_context install
```

The installer asks two things: where you use it (Claude Code, your own API code, or both) and whether Jev should be
involved. `--where`, `--jev`, `--yes`, `--dry-run` and `--uninstall` skip the questions. A TypeSafe key is only asked for
if you choose Jev; it is typed hidden, saved owner-only in `~/.jev-toolkit/.env` and never printed.

Without the installer, in Claude Code:

```text
/plugin marketplace add Wrenik/Jev-toolkit-Compiler
/plugin install jev-context@jev-toolkit-compiler
/plugin install jev-context-replace@jev-toolkit-compiler
```

### With your own API key (no Claude Code)

The same logic works on any Anthropic-style messages list:

```python
from jev_context.window import History

history = History("my-agent")           # saved in ~/.jev-toolkit/var/window/, or History() for memory only
messages = history.reduce(messages)     # before each request once the conversation is long
tools = my_tools + history.tools()      # search_history and get_history for the model
# when the model calls one of them:  result = history.call(name, input)
```

It makes no model call and no network request. Not calling `reduce()` is the off switch.

## Privacy and safety

* The log is local; secrets and e-mail addresses are redacted before anything is stored. Names and phone numbers in prose are not.
* Retrieved history is quoted as data, with a note that it may be outdated or wrong. A line in an old file that says
  "ignore your instructions" is just text the model was told not to obey.
* The mod only ever answers a compaction; it does not touch files, run commands or send anything anywhere except to
  the log server on `127.0.0.1`.

## Known limits

* The outline shows the first 140 characters of each prompt. A detail late in a long prompt is only found if the model
  decides to search.
* Retrieval is keyword search. A question that shares no words with the old text can miss it.
* The automatic (threshold) compaction and `$.session.compact()` paths are untested; only `/compact` was run.
* The row with the button is untested in the desktop app (it was only checked with `claude plugin validate/test` and headless).
