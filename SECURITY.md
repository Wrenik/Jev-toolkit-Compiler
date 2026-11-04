# Security policy

This is a learning project, not a security product. It stores a redacted copy of your Claude Code transcripts and lets an agent search it; read section 9 of `docs/CONTEXT_COMPILER.md` (privacy and limits) before trusting the redaction.

## Reporting a problem

* A secret format the redaction misses, or a way to make the quoted-data frame of the MCP results forgeable, is **welcome as a normal issue**: please use inert, made-up examples only.
* A vulnerability in the code itself (for example a way to make a local server read files, run commands, or leak a key): use GitHub's **private vulnerability reporting** (the repository's Security tab, "Report a vulnerability") instead of a public issue. If that button is missing, open an issue that says only that you have a security report and do not include details; I will answer with a private way to send it.

## What is and is not in scope

* In scope: the Python packages in `src/jev_context/`, the mod in `claude-mod/jev-context/`, and the documentation claims about them.
* Out of scope: TypeSafe's service, Claude Code itself, and attacks that need an attacker who already controls your machine.

## Secrets

Never put your `TYPESAFE_API_KEY` in an issue, a log excerpt or a screenshot. `.env` and `var/` are git-ignored, and by default the tools keep their data outside any repository in `~/.jev-toolkit` (`JEV_HOME`); the tools redact likely secrets before logging or sending text to the judge, but redaction is best effort.
