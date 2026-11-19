"""`python -m jev_context install`: a guided setup that never needs you to edit a file by hand.

Two questions (or flags): where do you use it, and do you want Jev to be involved?

  where:  claude-code  the mods (history in the log instead of a model-written summary, plus compact/clear advice)
          api          you call a model API yourself; use `jev_context.window.History` (printed at the end)
          both
  jev:    yes          the TypeSafe Jev judge decides routing and advice (needs a TypeSafe key)
          no           no key, no network judge: the replace feature and the search are local logic anyway

The key is read from the environment or typed hidden; it is written to JEV_HOME/.env (owner-only) and never printed.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

MARKETPLACE = "jev-toolkit-compiler"
PLUGINS = ("jev-context", "jev-context-replace")
GITHUB = "Wrenik/Jev-toolkit-Compiler"

Say = Callable[[str], None]


def _checkout() -> Path | None:
    """The repository this package was installed from, when it is an editable install (has .claude-plugin)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".claude-plugin" / "marketplace.json").exists():
            return parent
    return None


def _ask(prompt: str, options: list[tuple[str, str]], default: str, assume_yes: bool) -> str:
    if assume_yes:
        return default
    print(f"\n{prompt}")
    for key, text in options:
        print(f"  {key:<12} {text}{'   (default)' if key == default else ''}")
    while True:
        try:
            got = input("> ").strip().lower() or default
        except EOFError:
            return default
        if got in {k for k, _ in options}:
            return got
        print(f"  please type one of: {', '.join(k for k, _ in options)}")


def _run(cmd: list[str], say: Say, dry: bool) -> bool:
    say("  $ " + " ".join(cmd))
    if dry:
        return True
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"    failed: {exc}")
        return False
    out = (r.stdout + r.stderr).strip()
    if out:
        say("    " + out.replace("\n", "\n    ")[:600])
    return r.returncode == 0


def write_env(home: Path, key: str | None, judge_off: bool, say: Say, dry: bool) -> None:
    """Create JEV_HOME/.env without ever printing the key. An existing file is kept; only missing lines are added."""
    env = home / ".env"
    have = env.read_text(encoding="utf-8") if env.exists() else ""
    lines: list[str] = []
    if judge_off and "JEV_JUDGE=" not in have:
        lines.append("JEV_JUDGE=fake")
    if key and "TYPESAFE_API_KEY=" not in have:
        lines.append(f"TYPESAFE_API_KEY={key}")
    if "JEV_MODEL=" not in have and not judge_off:
        lines.append("JEV_MODEL=jev-1.14.0")
    if not lines:
        say(f"  kept {env} as it is")
        return
    if dry:
        say(f"  would add {len(lines)} line(s) to {env} (key hidden)")
        return
    home.mkdir(parents=True, exist_ok=True)
    with env.open("a", encoding="utf-8") as f:
        if have and not have.endswith("\n"):
            f.write("\n")
        f.write("\n".join(lines) + "\n")
    with contextlib.suppress(OSError):
        env.chmod(0o600)
    say(f"  wrote {len(lines)} line(s) to {env} (key hidden)")


def _python_on_path_has_us() -> bool:
    py = shutil.which("python") or shutil.which("python3")
    if not py:
        return False
    try:
        r = subprocess.run([py, "-c", "import jev_context, jev_core"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


API_SNIPPET = """
Use it with any Anthropic-style API (no Claude Code needed):

    from jev_context.window import History

    history = History("my-agent")                 # kept on disk between runs; History() keeps it in memory
    messages = history.reduce(messages)           # before each request once the conversation is long
    response = client.messages.create(model=..., messages=messages,
                                      tools=my_tools + history.tools(), ...)
    # when the model calls search_history / get_history:
    #   result = history.call(tool_use.name, tool_use.input)   # send back as the tool_result

Nothing here calls a model or the network. To switch it off, just do not call reduce().
"""


def install(
    where: str | None = None,
    jev: str | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
    uninstall: bool = False,
    source: str | None = None,
    say: Say = print,
) -> int:
    from jev_core.config import home as jev_home

    claude = shutil.which("claude")
    if uninstall:
        if not claude:
            say("claude was not found on PATH; nothing to remove.")
            return 1
        for p in PLUGINS:
            _run([claude, "plugin", "uninstall", f"{p}@{MARKETPLACE}"], say, dry_run)
        say(
            ("Would remove" if dry_run else "Removed")
            + " the mods. Your log and settings in JEV_HOME were left alone (python -m jev_context forget removes the log)."
        )
        return 0

    say(f"jev-context setup (python {sys.version.split()[0]}, claude {'found' if claude else 'not found'})")
    where = where or _ask(
        "Where do you use it?",
        [("claude-code", "the mods in Claude Code"), ("api", "my own code calling a model API"), ("both", "both")],
        "claude-code" if claude else "api",
        assume_yes,
    )
    jev = jev or _ask(
        "Should the Jev judge be involved? It decides routing and compact/clear advice and needs a TypeSafe key. "
        "Without it everything that replaces the summary still works.",
        [("yes", "use Jev (asks for a key if there is none)"), ("no", "no Jev, no key, nothing leaves this computer")],
        "no",
        assume_yes,
    )
    home = jev_home()
    say(f"\n1. Settings in {home}")
    key = os.environ.get("TYPESAFE_API_KEY") or None
    have_key = key is not None or (
        "TYPESAFE_API_KEY=" in (home / ".env").read_text(encoding="utf-8") if (home / ".env").exists() else False
    )
    if jev == "yes" and not have_key and not assume_yes:
        key = getpass.getpass("  TypeSafe key (hidden; press Enter to skip and add it later): ").strip() or None
    write_env(home, key if jev == "yes" else None, judge_off=(jev == "no"), say=say, dry=dry_run)
    if jev == "yes" and not (key or have_key):
        say(
            "  no key yet: add TYPESAFE_API_KEY to that file when you have one; until then Jev calls fall back to the local rules."
        )

    problems = 0
    if where in ("claude-code", "both"):
        say("\n2. The Claude Code mods")
        if not claude:
            say("  claude was not found on PATH. Install Claude Code first, then run this again.")
            problems += 1
        else:
            src = source or (str(_checkout()) if _checkout() else GITHUB)
            ok = _run([claude, "plugin", "marketplace", "add", src], say, dry_run)
            for p in PLUGINS:
                ok = _run([claude, "plugin", "install", f"{p}@{MARKETPLACE}"], say, dry_run) and ok
            if not ok:
                say(
                    "  something failed above. If a mod is already installed that is fine; otherwise run the lines by hand."
                )
                problems += 1
            if not _python_on_path_has_us():
                say(
                    "  note: the `python` on your PATH cannot import jev_context. Set the mod's python option, or set "
                    f"JEV_PYTHON to {sys.executable}"
                )
                problems += 1
        say("\n3. Read your past sessions into the log (local, secrets redacted)")
        if dry_run:
            say("  would run: ingest")
        else:
            from .ingest import ingest_tree
            from .store import connect, default_db

            say(f"  {ingest_tree(connect(default_db()), Path.home() / '.claude' / 'projects')}")
    if where in ("api", "both"):
        say(API_SNIPPET)
    say("\nDone." if not problems else f"\nFinished with {problems} thing(s) to fix (see above).")
    if where in ("claude-code", "both"):
        say(
            "The replace feature is OFF until you turn it on: restart Claude Code, then click 'Turn on' in the row above "
            "the prompt, or type /jevreplace on. Turn it off the same way if answers get worse."
        )
    return 1 if problems else 0
