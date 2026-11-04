# Jev-toolkit-compiler v0.1.0 (draft release notes)

First public release. **Alpha**: a learning project, tested offline on synthetic data and on the author's own
transcripts. The Claude Code mod has not been verified in the desktop app by anyone but the author.

## What it is
- A local, searchable log of your Claude Code transcripts (SQLite/FTS5), with secrets and e-mail addresses redacted
  before storing. Claude Code deletes transcripts after about 30 days; the log keeps what it ingested.
- A command line and a read-only MCP server (`search_log`, `get_turn`) that return short snippets framed as quoted
  data, never as instructions.
- A keep / compact / clear advisor for large contexts (advice only; it never runs `/compact` or `/clear`).

## Try it in two minutes (no key)
```
pip install -e ../Jev-toolkit-usage && pip install --no-deps -e ".[dev]"
python -m pytest
python -m jev_context ingest --dry-run
python -m jev_context search "some words you remember"
```

## Status honesty
- Search quality is measured with an easy proxy; vague human questions are untested.
- Cost-model weights are assumptions, not calibrated on subscription usage.
- Redaction is best effort: phone numbers, addresses and names in prose are not detected; `npm_` tokens were seen to
  slip through the shared redactor (tracked in Jev-toolkit-usage).
- The advisor sends the last prompts (redacted) to TypeSafe Jev; everything else is local.

## Known limits
Windows and Linux CI only (no macOS run); Python 3.11+; one machine's measurements.

## Suggested GitHub settings (not applied)
- Description: "Searchable local log of Claude Code transcripts with redaction, an MCP search tool and a keep/compact/clear advisor."
- Topics: `claude-code`, `claude-code-plugin`, `mcp`, `sqlite`, `fts5`, `llm-memory`, `python`, `learning-project`
- Tag `v0.1.0`; mark as pre-release; attach no build artifacts.
