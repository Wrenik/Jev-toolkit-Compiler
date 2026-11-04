# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/). While the major version is 0, minor releases may
change behaviour; they are listed here.

## [Unreleased]

### Added
- `docs/PRIOR_ART.md`: comparison with existing session-memory tools, compaction hooks, FTS5 and MCP practice.
- `jev_context stats` (per project, role/kind, top tools); `search --since YYYY-MM-DD` and `--offset`.
- MCP `search_log`: `project` and `offset` parameters, "next page" hint, read-only tool annotations.
- Empty-result messages now say why (empty log, filters too narrow, words not found).

### Changed
- BM25 column weights (body 1.0, tool name 0.5); hit markers are guillemets instead of square brackets.
- `__main__.main` split into per-command functions.
- Regression tests: npm token, `curl -u` and fullwidth e-mail are redacted before storage.

## [0.1.0] - unreleased

The numbers used inside the private monorepo this repository was cut from restart at 0.1.0 here.

### Added
- `jev_context`: transcript ingest into SQLite/FTS5 with redaction, search CLI (`--json`, `--explain`, filters),
  `get`, `status`, `doctor`, `setup`, `forget`, `tier` (retention), `--version`.
- Read-only MCP server (`search_log`, `get_turn`) with results framed as quoted data and angle brackets escaped.
- Keep/compact/clear advisor (cost model + Jev verdicts), local server shared with the router mod, automatic
  background ingest, the `jev-context` Claude Code mod.
- Issue and pull request templates, code of conduct, ruff and mypy configuration (both clean), CI runs both.

### Fixed (found in the pre-release review)
- Transcript lines that are valid JSON but not objects, records with odd `message` shapes or non-string ids no
  longer abort an ingest; they are counted as `malformed` or `skipped`.
- One unreadable transcript (locked, deleted meanwhile) is counted under `errors` and the rest still go in.
- Sub-agent transcripts were logged under the project name `subagents`; they now belong to their parent project.
  The Windows user name inside project folder names is masked.
- The log database uses WAL and a 30 s busy timeout, so a CLI ingest and the server no longer lock each other out.
- `search` printing a character the Windows console cannot encode crashed the CLI; output now replaces it.
- The MCP server decodes stdin as UTF-8 and survives non-object `params`/`arguments`, huge or boolean `days`.
- `get_turn` no longer fails when a blob row is missing (falls back to the clipped text).
- `pip install -e ".[dev]"` tried to clone the not-yet-public dependency; the docs and CI now use `--no-deps`
  after installing Jev-toolkit-usage.
