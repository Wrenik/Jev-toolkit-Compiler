# Prior art and what we took from it

Research date 2026-11-04. Sources were read through web search and a few official pages; where a claim is
second-hand (blog, issue tracker) it says so. Nothing here was copied; these are ideas.

## Session-memory tools for Claude Code

| Tool | Approach | Difference to jev_context |
| --- | --- | --- |
| claude-mem (thedotmack) | Plugin with lifecycle hooks; an LLM summarises tool use into "observations" kept in SQLite with FTS5, plus a Chroma vector index and a local web viewer. Search is a skill plus MCP tools with a token-saving 3-layer flow (index, timeline, full detail). | Summaries are model-written and lossy; ours keeps the verbatim (redacted) turn and searches it. We adopted the layered idea: snippets first, `get_turn` for full text. |
| codemem (third party) | SQLite FTS5 + BM25 plus sqlite-vec for hybrid retrieval. | Vectors need an embedding model; we stay keyword-only (no extra dependency, no network). |
| mem0 / OpenMemory MCP | Local memory layer for many MCP clients; vector store, needs Docker and an embedding key. Stores extracted "memories", not transcripts. | Different goal: curated facts vs. exact look-back after /compact. |
| basic-memory, ccusage and the various history viewers | Not verified by this round's searches (no results); ccusage is known to read the same `~/.claude/projects` JSONL for cost reports. | We share the input format only. Treat as untested. |

Take-away: the common pattern is hooks plus SQLite FTS5 plus an MCP search tool. Ours differs by being
deterministic (no model in the loop at ingest), redacting before storage, and framing results as quoted data.

## How compaction works (docs: code.claude.com hooks reference)

- `PreCompact` fires before compaction, matcher `manual` or `auto`; exit code 2 blocks it. `PostCompact` cannot block.
- `SessionStart` has matcher values `startup`, `resume`, `clear`, `compact`, `fork` and can inject `additionalContext`.
- Hook input carries `transcript_path`; the file is written asynchronously, so it can lag the live conversation.
- Issue tracker reports (second-hand): auto-compaction once skipped `PreCompact` (v2.1.105-2.1.114, closed June 2026).
  So the log must not depend on that hook: ingest runs at session start/end and on server start, and `ingest` is idempotent.
- "Context editing" as a separate Claude Code feature was not found; not claimed.

## SQLite FTS5 (sqlite.org/fts5.html)

- `bm25(table, w1, w2, ...)`: one real weight per column, leftmost first, default 1.0; lower is better.
  **Applied:** `bm25(turn_fts, 1.0, 0.5)` so a match in the body outranks one only in the tool name.
- `snippet(table, col, open, close, ellipsis, max_tokens)`, max_tokens 1..64.
  **Applied:** 32 tokens, then a hard 240 character cap; markers are guillemets, not `[ ]`, so a bracketed word in a transcript is not mistaken for a hit.
- Prefix queries (`word*`) work without a prefix index, only slower; `prefix='2 3'` would speed them at the cost of size. Not needed at current sizes.
- `detail=column|none` shrinks the index but loses phrase queries; we keep `full` because phrase search is a feature.
- `INSERT INTO t(t) VALUES('optimize')` merges b-trees (already run after `forget`); `integrity-check` is available for `doctor` later.

## MCP server practice (spec 2025-06-18 and community guides, the latter second-hand)

- Tool annotations are hints for clients, never security: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
  **Applied:** both tools declare read-only, non-destructive, idempotent, closed-world.
- Pagination: the spec uses opaque cursors for list operations; for a search tool the common practice is `limit` plus `offset`/cursor and a "has more" signal. **Applied:** `offset` parameter and a "next page: offset N" line; offset is clamped; past-the-end says so.
- Invalid parameters should be a clear error (spec: -32602 for protocol-level cursors); tool-level problems are `isError` results with readable text (already so).
- Bound output size: `limit` max 20, a token budget, snippet caps, `get_turn` capped at 6000 characters.
- `outputSchema`/`structuredContent`: one empirical report says Claude Code gives the model the same content with or without it, so no token saving. **Not adopted**; text stays the single output, which also carries the quoted-data frame.
- Tool descriptions say when NOT to call ("rarely and on purpose") to keep models from searching every turn.

## Not adopted, deliberately

Vector search, model-written summaries, a background daemon, and a web viewer: each adds a dependency, a network
path or a cost, against this project's local-only, deterministic position.
