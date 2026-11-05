# Context compiler, clear and long-term log (project 2)

Status: **in progress** (2026-11-03). Decisions a-d answered by the author (see end of section 7). Built and tested: `measure.py`, `cost.py` (step 1), `store.py` + `ingest.py` (step 2). Steps 3-5 not started.

> **Repository split (2026-11-04).** This document was written in the single `jev-toolkit` repository. It now lives in Jev-toolkit-compiler; the judge layer, settings and the router mod are in Jev-toolkit-usage. Data is stored under `JEV_HOME` (default `~/.jev-toolkit`; `var/` below means `<JEV_HOME>/var`). The mods no longer need a checkout path (`toolkitPath` / `JEV_TOOLKIT` are gone; they use `python` on PATH, the mod's `python` option, or `JEV_PYTHON`). The `.mcp.json` entry is now `python -m jev_context.mcp_server` with no path. Historical passages below describing the old launch config are kept as written.
Legend: **[measured]** = computed from the author's own transcripts with `python -m jev_context.measure`; **[verified]** = stated in Anthropic's docs (code.claude.com prompt-caching and costs pages); **[assumed]** = my assumption, to be checked.

## 1. The problem in one paragraph

A Claude Code conversation carries its whole history as context. That history is cached; when the cache is cold (main conversation: 1 h after the last call, subagents 5 min [verified]) the next message re-reads the entire context at cache-write price. The author's contexts are large, so a cold restart is expensive; but compacting also costs a request, and clearing loses information. Question: **at a given moment, which of "do nothing", "compact now", "clear and rely on a log" is cheapest, without losing what matters?** Separately: keep a complete, searchable log outside the context for exact look-back, and keep that log from filling the disk over years.

## 2. Prior art (research done 2026-10-30; licences read from each repo's page, not audited)

| Tool | What it does | LLM? | Licence | What differs from this project |
|---|---|---|---|---|
| [sqz](https://github.com/ojuschugh1/sqz) | Compresses tool output before it enters context; SHA-256 session dedup (`§ref:HASH§` instead of repeats); Claude Code hooks + MCP | No, deterministic Rust | Apache-2.0 or MIT (≤2.0.0 was Elastic-2.0) | Works *per tool call going in*; no cost model for cold/warm, no long-term log. A candidate to **use alongside**, not to copy. |
| [Headroom](https://github.com/chopratejas/headroom) | Content-routed compressors (JSON, AST-aware code, prose via a small trained model), cache-aware (never rewrites the cached prefix), reversible: originals stored locally, retrievable by tool | Small trained model for prose, not a generative LLM | Apache-2.0 | Closest in spirit (reversible compression + retrieval). Compresses on the way in; does not decide *when* to compact vs let the cache go cold. |
| [LLMLingua](https://arxiv.org/abs/2310.05736v1) (Microsoft) | Token-level prompt compression using a small LM's perplexity | Small LM (not generative output) | Reported as MIT by package indexes; **repo LICENSE not read by me** | Needs a local model + GPU-ish compute, lossy at token level; wrong granularity for code/tool output. Reference only. |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Hooks record sessions, an AI generates summaries, SQLite + FTS5 + Chroma, 3-step retrieval (search → timeline → get) | **Yes** (summaries via an AI provider) | Apache-2.0 | Memory across sessions via LLM summaries. We want verbatim log + non-LLM reduction + cost decision. Its 3-step *retrieval pattern* is worth echoing in our tool API. |
| [claude-session-search](https://pypi.org/project/claude-session-search/), [Memex (vimo-ai)](https://lobehub.com/mcp/vimo-ai-memex), [tim0120 history MCP](https://lobehub.com/mcp/tim0120-claude-code-history-mcp), [context-memory](https://enterprisedna.co/directories/mcp/erebusenigma-context-memory) | Index Claude Code transcripts into SQLite/FTS5, search via web UI or MCP | No | **not checked** | Prove the log-search idea works and is common. None has tiered retention, secret redaction at ingest, or the cold/warm decision. Memex notes the 30-day deletion of local transcripts, which motivates our own store. |
| [context-compress](https://github.com/vidanov/context-compress) | Regex compression of instruction files (CLAUDE.md) | No | not checked | Static files only. |
| Built-in `/compact`, `idleCompaction` | LLM summary by Claude itself | Yes | n/a | The baseline we compare against; costs one large request when cold [verified]. |

Take-aways: (1) non-LLM compaction of *tool output* exists and is mature (sqz, Headroom) — we should not rewrite it; (2) transcript → SQLite/FTS5 search exists many times over — our value is **redaction, tiering with measured thresholds, and the cost-aware decision**; (3) nobody found does the cold-vs-warm-vs-clear decision with a judge. Gaps in this survey: I did not read the source of any of these, and star counts / maintenance state were not checked.

### 2.1 Leads from a second web survey (2026-11-03) — **unverified, not yet read at source**
Passed on by another session; listed so they are not lost, with what each would change. None is relied on yet.

| Lead | Claim (as reported) | What it would change here |
|---|---|---|
| [claude-mem on PyPI](https://pypi.org/project/claude-mem/) | Reads `~/.claude/projects` JSONL into SQLite FTS5; MCP tools `session_list`/`session_search`/`session_show` | Confirms the pattern; our tool names can follow the same three-step shape |
| [memorex](https://github.com/mitre88/memorex) | FTS5 + BM25, decay scoring, hard token budgets, auto-injection | **Result token budget** and optional **recency decay** on search (plan step 3) |
| context-memory (ErebusEnigma) | FTS5 with tuned PRAGMAs (WAL, 64 MB cache) | Try WAL + cache size in step 3, measure |
| [claudecode-infinite-memory](https://www.remoteopenclaw.com/mcp/lchaoer/claudecode-infinite-memory) | Merges transcripts, memories and a knowledge base with merged ranking | Out of scope; noted |
| sqlite-memory-mcp | BM25 plus optional vectors via sqlite-vec | Vectors only if measured recall@5 demands it |
| [semantic-transcript-search](https://lobehub.com/en/mcp/ttrine-semantic-transcript-search) | Vector search, needs a running Qdrant | Heavier than FTS5; not planned |
| yoavf ai-sessions-mcp | One MCP over several coding agents | Not needed |
| [Agentic Context Management](https://arxiv.org/pdf/2607.23809) | Agent-initiated lossless compression; pays off only with a base model that sustains long tasks | Supports "reversible first"; no change |
| [Coding-agent scaffold taxonomy](https://arxiv.org/pdf/2604.03515) | SWE-agent's LastNObservations keeps the prompt cache intact by pruning in batches | Any pruning we suggest must happen at stable points, never every turn (already in section 5) |
| [morphllm write-up](https://www.morphllm.com/llm-context-management) | With caching on, keeping everything was often cheaper than summarising; capping every tool output at a stable size cut per-turn cost 38 % | Single vendor. Makes **"cap tool outputs"** a real option beside keep/compact/clear (see 4.5) |
| [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching) | Pluggable context engine, compressor replaceable | Keep the advisor behind an interface (it already goes through `Judge`) |
| [Compresr](https://ycombinator.com/companies/compresr) | Commercial compression API + open-source proxy | Sends context to a third party: conflicts with the local-only stance; not used |
| [compact cost analysis (dev.to)](https://dev.to/paul_irolla_6b5ae261d0224/claude-code-compact-what-it-costs-and-what-survives-2cfm), [when to compact (wmedia)](https://wmedia.es/en/tips/claude-code-when-to-compact-before-a-break), [cost model (dev.to)](https://dev.to/recca0120/does-a-long-claude-code-session-waste-tokens-a-cost-model-most-people-get-wrong-20f7), [compaction cascade (finisky)](https://finisky.github.io/en/claude-code-context-compaction) | Community posts: compaction is one big request so cost follows cache state; compact before a break while warm; clearing too often costs re-warming; Claude Code has a multi-layer compaction cascade | Consistent with the model in section 4. The cache-read/cache-write ratio is a possible churn signal (4.5). Read the cascade post before step 4 so we do not duplicate what Claude Code already does |

These sources disagree with each other on the cache TTL (5 min vs 1 h). The Anthropic-docs facts from phase 1 (main conversation 1 h, subagents 5 min) stand until a doc says otherwise. "Reversible compaction first" is reported as Anthropic guidance; **not verified** against Anthropic's context-engineering docs.

What this survey says about our position, stated plainly: indexing and recalling transcripts is well trodden (many tools, above and in section 2). The part we found nowhere is a cost-based **cold vs warm vs clear decision made with a judgement model**; everything else here is a re-implementation of known ideas, built for learning and for redaction/retention that the others lack.

## 3. What the data says [measured]

Source: 12 transcript files under `~/.claude/projects` (11 after removing one byte-identical fork), 23.6 MB, 611 main-chain API calls, 2026-10-01 … 10-30. **Small sample, one user, mostly three long sessions** — thresholds below are starting points, not findings about "users".

| Quantity | Value |
|---|---|
| Context per API call | median 263k tokens, p90 569k, max 726k |
| Gap between consecutive calls | median 12 s, p90 65 s, p99 64 min, max 75.7 h |
| Gaps > 5 min / > 1 h | 16 (2.7 %) / 7 (1.2 %); the >1 h gaps have median 3.4 h |
| Observed cold re-reads (call re-created >80 % of a >20k context) | 6, median 387k tokens each, 2.63 M tokens total |
| Cache tokens created vs read, all sessions | 5.68 M created, 177 M read → creation is 3.1 % of cache traffic, **but 46 % of all creation was these 6 cold re-reads** |
| Compactions | auto 343,780 → 39,251 tokens (−89 %); manual 728,023 → 18,737 (−97 %) |
| Tool results share of transcript text | 1.60 M chars vs 0.13 M assistant + 0.21 M user: **tool output is ~82 % of content** |
| Exact-duplicate tool results (>200 chars) | only 1.2 % of tool output → plain dedup (sqz-style) saves little *here*; truncation/summarising of big reads is where the volume is |
| Transcript size by record type | user 28 %, assistant 27 %, attachment 17 %, **api-request/-blob/-shape 25 %** (copies of request bodies; pure log overhead, droppable) |
| Whole-file compression | zlib-6 4.0×, zstd-3 6.8×, zstd-19 8.3×, lzma 8.5× |
| Log growth | ~23.6 MB over the month, heaviest day ~9 MB raw → ≈ 1.3 MB/day zstd-3 at the heavy end |

Honest limits: "cold" is inferred from usage numbers, not from an explicit flag; the 5-minute subagent gaps are not separated; the sample has only 2 compactions, so no distribution of compaction quality; retrieval quality of any tier is **not measured yet** (needs the Phase 2 store).

Reading: cold restarts are **rare (1.2 % of gaps) but dominate creation cost** because contexts are huge. So the interesting decision is the rare one: "I have a 400k context and the cache is about to expire (or has)".

## 4. Decision model

### 4.1 Cost units — what is verified, what is assumed
On a subscription, cost is **usage against a limit, not dollars**, and Anthropic does not publish the per-token weighting. I use *relative weights borrowed from API list ratios* purely as an assumption: cache read r = 0.1, cache write (1 h TTL) w = 2.0, uncached/other input 1.0, output o = 5.0, all per token, in "input-token equivalents". The break-even formulas below keep r, w, o as parameters; Phase 2 reads them from config and the plan includes calibrating them against the app's own usage numbers (`$.session.usage`, usage band) so they are not guesses forever. [verified]: separate cache per model; model switch or some setting changes re-read everything; main conversation cache 1 h, subagents 5 min; `/compact` is cheap-ish warm, a large request cold.

### 4.2 Options when context is C tokens, summary size S, expected future messages n
Let state be **warm** (cache alive) or **cold**.

- **A. Do nothing / continue.** Warm: each future turn costs r·C. Cold: the next turn costs w·C once, then r·C per turn.
- **B. Compact now.** The compaction call reads C (warm: r·C, cold: w·C) and writes a summary of S output tokens (o·S). The next turn then writes S fresh (w·S), and later turns cost r·S.
- **C. Clear and rely on the log.** Cost ≈ w·S₀ where S₀ is the tiny re-seed (a few k tokens: goal, constraints, pointers). Loses everything not in the seed; recoverable only through log search. Chosen when most of C is *not load-bearing*.

Per-turn saving after compacting is r·(C − S). Extra one-off cost of B over A:
- warm: Δ = o·S + w·S (the r·C read is paid by A too on its next turn, so ≈ cancels) 
- cold: Δ = o·S + w·S as well (A pays w·C once, B pays w·C inside the compact call instead)

so **break-even number of future turns n\* = 1 + (o + w)·S / (r·(C − S))**, where n counts the next turn too (corrected 2026-11-03: the first draft of this formula left out the `1 +`, which made every value below one turn too low; the numbers below are the corrected ones). With C = 263k (median), S = 20k, o=5, w=2, r=0.1: n\* = 1 + 140k / 24.3k ≈ **6.8 turns**. With C = 569k (p90): ≈ 3.6 turns. With C = 60k: ≈ 36 turns. So under these (assumed) weights compaction pays off only when context is large *and* the user will keep going; at small C leave it alone. The same formula says compacting a *cold* session buys nothing extra over letting the next message re-warm it, unless n > n\* — the only reason to compact cold is that n is large (or context quality/limit pressure, §4.4).

Note the "wait" option has a time dimension: if the cache is warm and the user is idle, waiting costs nothing until the TTL, then A-cold applies. An early compact while warm vs a later one cold costs the same Δ here (a premise worth verifying: compact-while-cold may also re-create the cache for the prefix, making it slightly better than I model).

### 4.3 What plain arithmetic covers, what needs Jev
| Needed input | Source |
|---|---|
| C, TTL remaining, model, idle time | `$.session.usage`, timestamps — plain code |
| S (summary size) | estimate from measured ratios (−89 % … −97 % ⇒ S ≈ 0.03–0.11·C) — plain code |
| r, w, o weights | config, calibrated (§4.1) |
| n = expected number of further turns in this task | **Jev** (below) — not knowable by arithmetic |
| fraction of C that is load-bearing | **Jev** per chunk, or cheap heuristics first (tool output older than k turns, files since re-read) |
| items that must survive verbatim | **Jev** scores candidates; code extracts them |

Jev's typed questions (one `state` per question: last user prompts + task heading; never raw secrets — run the router's redaction first):
1. `YesNo`: "Will the user most likely continue this same task within the next hour?" → P_continue. Expected n comes from a small table keyed by this probability and the Score below (e.g. P>0.8 and task=ongoing ⇒ n≈10).
2. `Score` 1–5: "How much of the earlier conversation is still needed for the next steps?" → load-bearing level.
3. `Choice` ["task finished", "paused mid-task", "topic switch"]: closure state. Finished/switch ⇒ option C (clear) becomes attractive.
4. For each candidate item (decision, constraint, file path, TODO, error that was resolved): `YesNo` "must this be kept verbatim in a summary?" Batched, ≤32k tokens per question.

Everything Jev returns is advisory input to the formula; fail-open (judge down ⇒ don't compact, show nothing). Decision output: one of `keep`, `compact_now`, `compact_before_ttl(minutes)`, `clear_with_seed`, with the numbers shown.

### 4.4 Non-cost reasons to compact
Context limits and long-context accuracy (the 726k-token session is near the limit) — compacting early may be right even when n < n\*. Config switch: `max_context_tokens` above which the answer is "compact" regardless of arithmetic.

### 4.5 Additions after the second survey (to build in steps 3-4)
- **Cap tool outputs** becomes a fourth option next to keep / compact / clear: a cap at a *stable* size, applied going forward only (never rewriting the cached prefix). Our own number to check first: tool output is ~82 % of transcript text [measured], so a cap has the largest lever. The 38 % figure in the lead is one vendor's claim; we measure our own with the stored log (what share of tool-result characters sit above a cap of 8k / 16k / 32k).
- **Churn signal:** cache writes divided by cache reads over the last N calls, computed from transcripts (plain code, no judge). A high ratio means the session keeps re-warming, which argues against clear and for keep. Measured value for the author's data: 3.1 % created vs 96.9 % read overall.
- Both inputs are optional fields of `Situation`; unknown means "no effect", same fail-open rule as the judge answers.

## 5. Reducing context without an LLM
Order of operations (cheapest first), all deterministic, applied by *us* only to the **log and to compaction input**, never by rewriting cached history in place (that would break the cache; Headroom makes the same point):
1. Drop duplicate tool results by hash (measured gain small: 1.2 %).
2. Truncate big tool results (file reads, logs) to head + tail + hash, original kept in the log (reversible; same idea as Headroom/sqz).
3. Remove resolved errors and superseded file versions.
4. Only then summarise: Claude's own `/compact` (via `$.session.compact`, no API key needed) or a local model. Jev only chooses *what* and *when*.

## 6. Long-term log design

**Store:** SQLite, `<JEV_HOME>/var/log/jev-context.sqlite`, local only. Tables: `session(id, project, started, model)`, `turn(id, session_id, seq, ts, role, kind, text, tokens, redacted)`, `blob(id, turn_id, sha256, zstd_bytes)` for large tool output, FTS5 virtual table over `turn.text` (`unicode61`, trigram optional). Ingest from the transcript JSONL (and later from `turn.complete`), **excluding `api-request*` records (25 % of bytes, no information not already present)**.

**Redaction before write:** reuse the shared `jev_core.redact` patterns (moved there from `jev_router.redact`, which is now a thin re-export) (API keys, tokens, `.env` lines, private key blocks); store a count of redactions, never the secret; test with seeded fake secrets.

**LLM access:** an MCP server / mod tool with the 3-step pattern: `search_log(query, limit)` → ids + snippets; `get_turn(id, context=2)`; `get_blob(id)`. Output size capped so a search can't re-inflate the context.

**Tiering — proposal, to be validated in Phase 2:**
| Tier | Age | Form | Why |
|---|---|---|---|
| hot | 0–30 days | rows in SQLite, text uncompressed, FTS5 index | exact, fast; the 30-day mark also matches Claude Code's own transcript deletion |
| warm | 30 days – 2 years | per-session zstd-19 blob of the full turns (≈ 8×), FTS index kept for text of user/assistant turns only | measured 6.8–8.3× whole-file; tool bodies searchable only after un-pack |
| cold | > 2 years | user + assistant text kept (zstd/lzma), tool results reduced to head/tail + hash + size, original dropped; optional LLM/local summary per session | tool output is ~82 % of text, so this is where the space is; lossy and irreversible, hence late and opt-in |

Disk arithmetic with the measured numbers: ~1.3 MB/day at the heavy end ⇒ ≈ 0.5 GB per year in warm form, more like 0.05–0.15 GB/year at typical days. **The data says disk is not urgent**; the multi-year cold step is a hygiene decision, so default stays "nothing is deleted, only compressed" (as in DESIGN §6.2) and the cold tier ships disabled. FTS index size, retrieval quality per tier and the lossy-tier information loss are **not measured** — Phase 2 measures them (index overhead as % of text; recall of 30 known-answer queries before/after each tier).

## 7. Implementation plan (for approval)

Smallest useful slice, in order, each with offline tests and one live run:
1. **`jev_context.cost`** — the §4 arithmetic as pure functions (`break_even_turns`, `decide`), config for r/w/o, tests with the worked numbers above.
2. **`jev_context.ingest` + `store`** — transcript JSONL → SQLite+FTS5, drop `api-request*`, redaction, idempotent by record uuid; tests with synthetic transcripts and seeded secrets; run on the author's 11 real sessions and report size, ingest time, FTS overhead.
3. **`jev_context.search`** — CLI `python -m jev_context search "…"` plus `get_turn`; **BM25 ranking, a hard cap on result tokens, optional recency decay, WAL pragmas (each measured, kept only if it helps)**; evaluation: 30 hand-made known-answer queries against real sessions → recall@5.
4. **`jev_context.advisor`** — builds the 4 Jev questions through `jev_core.Judge` (with `FakeJudge` in tests), feeds `cost.decide`, logs every decision via the existing decision log; live run against Jev (`python -m jev_core smoke` first). **Advisory only** — shows "compact now / let it cool / clear" and the numbers; no auto-compact in this slice.
5. **Tiering job** (`warm` only) and measured compression table; `cold` stays designed-not-enabled.
6. **Later, separate approval:** mod integration (status line + `compact` button using `$.session.compact`), MCP tool for the LLM, auto-compact before TTL.

Out of scope for the slice: local-LLM summariser, semantic/vector search, cold-tier deletion.

Decisions answered 2026-11-03: (a) yes, weights configurable and marked as assumed; (b) yes, 30 days / 2 years, nothing deleted by default, cold tier disabled; (c) MCP search tool after the advisor; (d) first-party code only for now, sqz/Headroom documented as tools to run alongside.

Original questions: (a) accept the assumed r/w/o weights for now, calibrate later? (b) hot window 30 days and warm/cold boundary at 2 years OK as defaults? (c) MCP tool (LLM can search) in this slice or after the advisor? (d) use sqz/Headroom alongside later, or keep everything first-party? 

### 7.1 MCP search tool (built 2026-11-03, after the advisor as the author decided)
The author's earlier answer to (c) was "after the advisor". The master session reports a new requirement that the agent itself must be able to search the log on its own, which would make the MCP tool part of the first slice. **Built** as `jev_context.mcp_server` (tools `search_log`, `get_turn`; tests in `tests/test_context_mcp.py`). Design:
1. **Server:** stdio MCP server, own code (`jev_context.mcp_server`), read-only, opens the SQLite file read-only. Tools: `search(query, limit, since, project)`, `get_context(id, around=2)` (full text only here, capped), `list_sessions(limit)`.
2. **Budget:** hard cap on result tokens (default ~1,500) and short snippets by default, so one search cannot blow up the context or cached prefix.
3. **Description tells the model when to search:** exact look-back after a compact or clear, "what did we decide about X"; rarely and on purpose, not at every turn.
4. **Secrets:** redacted at ingest (step 2), so no tool can return them; the server never reads transcripts directly.
5. **Untrusted data:** every result is wrapped and labelled as a quote from a past session, never as instructions (transcripts can contain injected text).
6. **Install:** a plugin `.mcp.json` entry launching `python -m jev_context.mcp_server`; runs locally under the Pro subscription, no API call.
7. **Tests:** synthetic transcripts only, including a seeded-secret test and a test that an injected "ignore previous instructions" line comes back marked as quoted data. Decision: hand-written JSON-RPC over stdio (about 150 lines, no dependency). Limits: the Windows venv path in `.mcp.json` is hard-coded, and `${user_config.toolkitPath}` substitution inside `.mcp.json` is **unverified** until the plugin is loaded in Claude Code; `list_sessions` was dropped from v1.

## 8. Status 2026-11-03 and what is still unverified

Built and tested offline (no API key): `cost`, `ingest`/`store`, `search`, `advisor`, `jev_core.joint`, `broker`, the shared `server`, and the `claude-mod/jev-context` hook logic. Live against Jev: the advisor, and one shared request carrying router + context questions (both answered correctly).

**Search quality [measured, easy proxy]:** 30 turns per kind sampled from 13 real transcripts; the query is 2 or 3 of the turn's rarest words; success = the turn is in the top 5. Result: recall@5 0.97-1.00 for text, tool calls and tool results (recall@1 0.83-0.97); median query time 0.3 ms. This is **self-retrieval with rare words**, which is the easy case: it shows the index and ranking work, not that a human's vague question ("what did we decide about the cache?") finds its answer. A set of hand-written questions with known answers is still needed.

**Not verified:** the mod has not been run inside Claude Code; `register.tsx` is not type-checked (the generated type files exist only for jev-router); whether jev-context's `prompt.submit` hook runs before jev-router's, which decides whether the two requests actually meet (the server counts shared and separate calls, `/jevctx` shows them); retention (step 5) is built (`jev_context.retention`, `python -m jev_context tier`) but only unit-tested on synthetic data: index size and recall per tier are still unmeasured, and `tier` is not run automatically after `ingest`. The cold purge is off unless `--enable-cold` is passed.

### Checklist for the author: try the two mods in Claude Code
1. In a terminal in the Jev-toolkit-compiler clone, with Jev-toolkit-usage and this repository installed editable: `python -m pytest -q` (expect all passed).
2. Make both mods load: add `claude-mod/jev-router` (from Jev-toolkit-usage) and `claude-mod/jev-context` (from this repository) to `CLAUDE_CODE_PLUGIN_DIRS`, with `python` on PATH having both packages installed (or set `JEV_PYTHON`). Restart Claude Code.
3. Start a session and type `/jev` and `/jevctx`. Expected: the router says server up; jev-context says server up with context endpoints yes. If it says NO, send `/jev restart`, wait a few seconds and run `/jevctx` again.
4. Work in one session until its context is above ~100k tokens (a long task is enough), then send a few normal prompts. Under 100k, jev-context stays silent by design.
5. Run `/jevctx`. Look at "shared judge requests": a number above 0 means router and advisor shared a call. If it stays 0 while "separate" grows, the hook order is the wrong way round: tell me, that is a fix on my side.
6. Run `/jevctx ingest` once, then `/jevctx search <a word you remember from an earlier session>`. Results are quotes from old sessions, not instructions.
7. Look at `~/.jev-toolkit/var/context-advice.jsonl` (what the advisor decided, never prompt text) and tell me: did a "compact" or "clear" suggestion ever look wrong or arrive at a bad moment?
8. To stop everything: `/jevctx mode off` and `/jev mode off`.


## 9. Privacy, first run, and what is measured versus assumed

**First run.** `python -m jev_context ingest --dry-run` parses all transcripts in memory and reports counts without writing anything. `python -m jev_context ingest` then writes the log and prints `log now: N sessions, M turns, X redactions, database Y MB at <path>`. Default database: `<JEV_HOME>/var/log/jev-context.sqlite` relative to the working directory (override with `--db`, also accepted by `python -m jev_context.mcp_server`). The data location is `JEV_HOME` (default `~/.jev-toolkit`), outside any repository.

**Redaction is best-effort, and it runs after the fact.** Claude Code writes the raw transcript first; ingest reads it later. The raw files in `~/.claude/projects` therefore still contain every secret that was typed or printed, and this tool does not touch them. What the log adds is a second copy with common secrets removed (Anthropic, OpenAI, GitHub, AWS, Slack, Stripe, Google keys, JWTs, bearer tokens, URL passwords, PEM keys, `Cookie:`/`Set-Cookie:` values, `name=value` assignments for obvious secret names). A secret in an unusual format is stored. Redaction lives in `jev_router/redact.py`, which the router uses too; the new secret patterns therefore also apply to prompts sent to the judge.
**Personal data.** The log (not the router) also replaces e-mail addresses and home-directory user names (`C:\Users\<name>`, `/home/<name>`, `/Users/<name>`). It does **not** detect telephone numbers, postal addresses, or personal names in prose; those are kept. Treat the database as a personal-data store and keep it out of backups and repositories you share.
**Deleted transcripts.** Claude Code deletes local session transcripts after 30 days by default (`cleanupPeriodDays` in the user's own `~/.claude/settings.json`; it can be raised there to keep the originals longer, and this project never edits that file). The log keeps what it ingested, but only if ingest ran before the deletion. Since 2026-11-04 the mod ingests automatically at session start and end and the server once at its start (background thread, idempotent, at most once per 3 minutes, failures only logged), so a manual `ingest` is no longer needed; the earlier advice to run it monthly is obsolete. Raising `cleanupPeriodDays` keeps the raw transcripts, which still contain unredacted secrets.
**Quoted data.** MCP results escape `<` and `>` in past text and sit inside a boundary with a random per-response token, so a hostile past turn cannot close the frame. This reduces, and does not remove, prompt-injection risk: the model still reads the quoted text.

**What this tool does NOT do yet (measured vs assumed)**

| Claim | Status |
|---|---|
| Tests pass (offline, synthetic data) | measured |
| Search recall@5 0.97-1.00 | measured on the author's own transcripts with an easy proxy (rare words of the turn itself); vague human questions are untested |
| Break-even turns, compact/clear advice | computed from **assumed** weights (r 0.1, w 2.0, o 5.0) and an assumed summary ratio; not calibrated on Pro usage |
| Joint router + context request | works against live Jev in one test; whether it happens in real use depends on hook order, **unverified** |
| Mod loads and hook fires in Claude Code | **unverified**; `register.tsx` is not type-checked |
| MCP server launch in `.mcp.json` | **Verified in a headless Claude Code session (2026-11-04).** It used `${user_config.toolkitPath}` plus a Windows-only venv path, which was empty/wrong unless the option was configured, so the server failed with CONNECTION_CLOSED. It now runs plain `python -m jev_context.mcp_server` (standard library only) with `cwd` and `PYTHONPATH` taken from the `JEV_TOOLKIT` environment variable, which README already requires. Needs `python` on PATH. |
| Retention: index size and recall per tier | **not measured**; `tier` is manual |
| Phone numbers, addresses, names in prose | **not redacted** |


## 10. Research round 2 (2026-11-03): what comparable tools do, and where we stand

Sources were read through search snippets and one page fetch each for claude-mem and claude-session-search; the rest are listing pages only. Star counts, maintenance state and the quality of any tool are **not** verified; descriptions of other tools are what their own pages say.

| Area | What others do (source) | Us before | Us now |
|---|---|---|---|
| Onboarding | claude-mem: `npx claude-mem install` or two `/plugin` commands, then restart; `claude-mem doctor`; web viewer to see it working ([repo](https://github.com/thedotmack/claude-mem)). claude-session-search: `uvx claude-session-search`, opens a local page ([PyPI](https://pypi.org/project/claude-session-search/)). tim0120's history MCP needs a one-time `index_conversations` call ([listing](https://lobehub.com/mcp/tim0120-claude-code-history-mcp)) | clone + `.venv` + several steps | `python -m jev_context setup`: ingests, runs `doctor`, prints the exact `claude mcp add ...` line for this machine. `doctor` says OK/WARN/FAIL with a fix. **Not** one-shot: we print the registration command instead of running it, because it edits Claude Code's config. No PyPI package, no web viewer. |
| Search UX | claude-session-search: project / role / date filters, sort by relevance or recency, automatic prefix, diacritic-insensitive, `"exact phrase"`, result opens the whole conversation with the match centred. Memex: `search_history`, `get_recent_sessions` ([listing](https://lobehub.com/de/mcp/vimo-ai-memex)). claude-mem: 3-step `search` -> `timeline` -> `get_observations` to save tokens | words ANDed, prefix `w*`, project/date filters in CLI only | added `"exact phrase"`, `--role`, `--kind`, `--tool` (CLI and MCP), `--explain` (BM25 score), a "no matches" hint with what to try, shown-vs-total. Skipped: diacritic folding (FTS5 `remove_diacritics` would need a re-index; cheap later), recency sort (we have `--half-life`), a UI. |
| Privacy and safety | claude-mem: `<private>` tags keep text out of storage; default observer model is hosted, so content leaves the machine ([repo](https://github.com/thedotmack/claude-mem)). claude-session-search: binds 127.0.0.1, no outbound requests, index at `~/.cc-search/index.db`, no delete command documented. agsearch warns Claude Code deletes transcripts after 30 days ([PyPI](https://pypi.org/project/agsearch/)) | redaction at ingest, read-only MCP, DB gitignored; no delete command | added `status` (prints the database path and size), `forget --session/--project/--all --yes` (dry run without `--yes`; transcript files are never touched). Skipped: a `<private>` tag, because transcripts are written by Claude Code and we cannot make it omit text; the honest answer is "redaction is after the fact" (§9). |
| The advisor | none found that decides compact/clear with a judgement model (survey, §2) | reason sentence with numbers (`saves 12,300 weighted tokens over 5.0 turns (break-even 9.7)`) | unchanged; it appears as a toast only for compact/clear, `keep` stays silent, `/jevctx` shows the last advice and why, `/jevctx mode off` silences it, and when Jev is down it does nothing (keep) unless the hard ceiling forces a compact (`fail_open`). |
| Performance | not published by the others | measured on a small sample only | see 10.2 |
| Things users expect that we lack | semantic/vector search (claude-mem uses Chroma; Memex roadmap), automatic injection of past context into new sessions (claude-mem), a web viewer, AI-written summaries, multi-tool support (Claw Recall indexes several agents) | all absent | **Deliberately skipped**, see 10.3 |

### 10.1 What leaves the machine

Nothing from the log or the MCP server: ingest, search, `get_turn`, `forget`, `tier` are local SQLite operations and the MCP server makes no network calls. The only outbound traffic is to TypeSafe (Jev), and only from the **advisor** (and the router, which is a separate tool): the last up to 5 prompts of the current session, redacted with `redact`, plus the question set, when the context is above 100,000 tokens, at most once per 5 minutes, and only while the mode is `advise`. The decision log `var/context-advice.jsonl` stores a hash of those prompts, not the text. If the router is also installed, its prompt and questions travel in the same request.

### 10.2 Performance on a large synthetic log

Generated by `tools/bench_context.py` (random words, 12 projects, text / tool calls / tool results); no real transcript was used.

Machine: Windows 11, 8 logical CPUs, Python 3.14.6, local SSD. 1,004 MB of transcripts, 314 files, 189,573 records.

| Measure | First run | After optimising |
|---|---|---|
| Ingest | 971 s (1.0 MB/s) | **269 s (3.7 MB/s)** |
| Re-ingest, nothing changed | 0.15 s | 0.08 s |
| Database size | 1,153 MB (1.15x) | 1,175 MB (1.17x) |
| Search, one word of the 20k-word vocabulary: median / p95 | 138 / 394 ms | 126 / 238 ms |
| Search, two such words | 14 / 24 ms | 12 / 15 ms |
| Search, a very common word (in about half of all turns): median / p95 / max | 811 / 1,832 / 3,679 ms | 826 / 1,031 / 3,229 ms |

The profile of the slow first run showed 52 % of the time in redaction regexes and 35 % in zstd level 19. Fixes: a cheap substring gate before each redaction pattern (identical output, checked by a randomised equivalence test) and zstd level 6. **Caveats:** the corpus is random words with a small vocabulary, so "rare" words still match thousands of turns and the search numbers are harsher than real text for rare words and unrepresentative for common ones; real transcripts compress and index differently. Searching a word that occurs in half of all turns is slow (about 0.8 s) because BM25 ranks every match; the size cap on the answer does not help that, and there is no fix yet (ideas: cap candidates with `LIMIT` before ranking, or drop stop-word-like terms). The log is about the same size as the transcripts, not smaller; the warm tier is what shrinks old tool output.

### 10.3 Deliberately skipped, and why
- **Semantic / vector search and AI summaries:** needs an embedding model or an LLM call per turn; either breaks "nothing leaves the machine" or adds a dependency. FTS5 + exact phrases covers the look-back use case we measured; revisit if hand-written questions show it failing.
- **Automatic injection of past context into new sessions:** the main thing claude-mem does. It spends context on every session start, which is exactly what this project tries to avoid; the MCP tool lets the agent ask on purpose instead.
- **Web viewer:** a second local server and UI is a lot of surface; the CLI and MCP cover the need for now.
- **PyPI package / one-command install:** would need packaging and a publishing decision that belongs to the repository owner.
- **Diacritic folding, recency sort, subagent tagging:** cheap but not needed for the stated use; listed so they are a choice, not an oversight.
