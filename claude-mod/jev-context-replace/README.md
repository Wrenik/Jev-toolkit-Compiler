# jev-context-replace (experimental, off by default)

When Claude Code compacts a conversation, this mod answers the `session.compact` hook itself. The model does not write a summary. The conversation becomes:

1. one message with a **local outline** of what was taken out (every prompt in short, the end of each answer, the files touched; built by code, no model),
2. optional **excerpts** the jev-context log returned for the current task,
3. the newest two prompts and everything after them, word for word, with **old tool output cut to a stub** once that tail is over 40 000 characters.

Everything taken out stays in the jev-context log, and the message tells the model to look details up with `search_log` and `get_turn`. The jev-context server must be running (the `jev-context` mod starts it). The part about to be dropped is ingested first; if that fails, or the conversation is short, or anything else goes wrong, the engine compacts as usual.

Why, what it measured and what it did not: [docs/CONTEXT_REPLACE.md](../../docs/CONTEXT_REPLACE.md).

## On and off

A row above the prompt shows `Jev context: off/ON` with a **Turn on / Turn off** button. Or type `/jevreplace on`, `off`, `toggle`, `now` (compact between turns), `status`. The choice is remembered between sessions; `JEV_REPLACE=1` only sets the default for someone who never chose.

## Install

```text
/plugin marketplace add Wrenik/Jev-toolkit-Compiler
/plugin install jev-context@jev-toolkit-compiler
/plugin install jev-context-replace@jev-toolkit-compiler
```

or `python -m jev_context install` (guided; with or without Jev).

## Tested

* `claude plugin test` (13 tests) and `claude plugin validate` pass.
* Headless on throwaway sessions: the summariser is skipped on `/compact`, and a secret placed deep in an old prompt was found again through `search_log`. `/jevreplace on|off|status` keeps its state across runs.

## Not tested

* The button row in the desktop app; long real working days; quality of answers afterwards; usage on the subscription meter.
* Prompt-cache cost of the one re-read after a compaction.
* Automatic compaction at the threshold (only `/compact` was run) and `$.session.compact()`.
