"""Launchers must reference commands that exist, and must survive a dumb console.

Two production incidents came out of this one gap, and neither had a gate:

1. `apps/shell/electron/main-openvault.js` spawned `openmw serve`. There is no
   `serve` command, so the child exited 2 on every cold start, the readiness
   check timed out, and the window opened against a backend that was never
   started. Nothing in the suite referenced the launcher at all.
2. `apps/cli/openvault_cli.py` printed a U+2192 arrow. Run from a UTF-8
   terminal it worked; spawned by a launcher with redirected or detached
   stdout, Python picked the Windows ANSI codepage and the process died at the
   log line before binding :5000 (R-0012).

Both are the same class: a launcher is executable code that no test executed.
These assertions are cheap, and they run against the real files rather than a
copy, so a new launcher is covered the moment it is added to LAUNCHERS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every file that shells out to the `openmw` CLI to bring the stack up.
LAUNCHERS = (
    "apps/shell/electron/main-openvault.js",
    "apps/cli/openvault_cli.py",
    "scripts/windows/Start-NetieStack.ps1",
    "scripts/windows/Start-LocalMesh.ps1",
)

#: Files a launcher runs with Python, whose stdout may be redirected or
#: detached. These must encode under the Windows ANSI codepage or they die
#: before doing any work.
ASCII_CRITICAL = (
    "apps/cli/openvault_cli.py",
    "OpenMW/scripts/one_seat_demo.py",
)

#: `openmw` followed by the next bare word, across quotes, commas and newlines
#: so it matches both `openmw console --port` prose and a JS/PowerShell argv
#: array like `"openmw",\n  "console",`.
_SUBCOMMAND = re.compile(r"""openmw["']?[,\s]+["']?([a-z][a-z0-9-]*)""")

#: Line comments, stripped before scanning. Without this the regex walks past a
#: comment sitting between `"openmw",` and the argument, finds nothing in the
#: argv, and then happily matches a *valid* command name in unrelated prose
#: further down the file - so the test passes while guarding nothing. That is
#: not hypothetical: it is what this gate did on its own first mutation run.
_LINE_COMMENT = re.compile(r"^[ \t]*(?://|#).*$", re.MULTILINE)


def _without_comments(source: str) -> str:
    return _LINE_COMMENT.sub("", source)


def _real_commands() -> set[str]:
    """The command names Typer actually registers, read from the live app."""
    from typer.main import get_command

    from openmw.cli import app

    return set(get_command(app).commands)


def _existing(rel: str) -> Path:
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.fail(
            f"{rel} is listed in this test but does not exist. If it was renamed or "
            f"deleted, update the list here - silently dropping a launcher from the "
            f"gate is how the `openmw serve` bug survived."
        )
    return path


@pytest.mark.parametrize("rel", LAUNCHERS)
def test_launcher_only_spawns_openmw_commands_that_exist(rel: str) -> None:
    """A launcher naming a command Typer does not register is a dead stack.

    The failure is silent and far from the cause: the child exits 2, the
    readiness poll times out, and the UI opens against nothing.
    """
    commands = _real_commands()
    source = _without_comments(_existing(rel).read_text(encoding="utf-8"))

    referenced = {
        word
        for word in _SUBCOMMAND.findall(source)
        # Flags and the package's own directory name are not subcommands.
        if not word.startswith("-") and word not in {"openvault", "openmw"}
    }
    assert referenced, (
        f"{rel} is listed as a launcher but no `openmw <command>` was found in it. "
        f"Either the spawn moved, or this test's regex stopped matching it - both "
        f"mean the gate is no longer guarding anything."
    )

    unknown = sorted(referenced - commands)
    assert not unknown, (
        f"{rel} spawns `openmw {unknown[0]}`, which Typer does not register. "
        f"Real commands: {sorted(commands)}."
    )


@pytest.mark.parametrize("rel", ASCII_CRITICAL)
def test_launcher_output_survives_a_non_utf8_console(rel: str) -> None:
    """R-0012 as a gate rather than a habit.

    cp1252 is what Python picks when stdout is redirected or detached on
    Windows. A single unencodable character anywhere in the file - including a
    docstring argparse renders into --help - kills the process at its first
    print, long before anything useful happens.
    """
    source = _existing(rel).read_text(encoding="utf-8")
    try:
        source.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = source[exc.start : exc.end]
        line = source[: exc.start].count("\n") + 1
        pytest.fail(
            f"{rel}:{line} contains {bad!r} (U+{ord(bad[0]):04X}), which cannot be "
            f"encoded under cp1252. Use a plain substitute: ' - ', '->', '...', \"'\". "
            f"This is the exact failure that stopped the demo stack from starting."
        )


def test_the_cli_reconfigures_stdio_before_it_prints_anything() -> None:
    """The robust half of R-0012, which this repo did not have.

    Substituting plain characters is the fragile half: it holds only until
    somebody types an em dash again, and it cannot protect text produced
    elsewhere and printed through the CLI. Reconfiguring the streams to UTF-8
    with ``errors="replace"`` kills the whole class - output can no longer be
    fatal, whatever ends up in it.

    Asserted by actually driving a cp1252 stream, not by grepping for the call:
    a guard that is present but does not work is worse than none.
    """
    import importlib.util
    import io as _io
    import sys as _sys

    cli_path = _existing("apps/cli/openvault_cli.py")
    spec = importlib.util.spec_from_file_location("openvault_cli_under_test", cli_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "ensure_utf8_stdio"), (
        "apps/cli/openvault_cli.py no longer defines ensure_utf8_stdio(). Without it a "
        "single unencodable character kills the launcher before it binds :5000."
    )
    assert "ensure_utf8_stdio()" in cli_path.read_text(encoding="utf-8").split("def main(")[1], (
        "ensure_utf8_stdio() is defined but main() does not call it first. It must run "
        "before argparse, because --help renders the module docstring."
    )

    real_stdout, real_stderr = _sys.stdout, _sys.stderr
    try:
        _sys.stdout = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
        _sys.stderr = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
        with pytest.raises(UnicodeEncodeError):
            print("proof the stream really is cp1252: →")
        module.ensure_utf8_stdio()
        print("after the guard: → — …")  # must not raise
        _sys.stdout.flush()
    finally:
        _sys.stdout, _sys.stderr = real_stdout, real_stderr
