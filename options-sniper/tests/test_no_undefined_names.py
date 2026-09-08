"""Every module, checked for names it uses but never defines or imports.

scheduler.py shipped `state.capacity_left()` with `state` unimported. The
unit tests all passed — nothing imported the scheduler — and the service died
on the first heartbeat after the open. A NameError on a line that only runs
once an hour is the worst kind of bug here: silent, total, and invisible to
every test that exercises the parts rather than the whole.

This runs pyflakes over the package so that class of bug cannot ship again.
It fails only on undefined names; unused imports and idle f-strings are
style, not an outage, so they are not gated.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Anything that is a real break at runtime rather than a tidiness note.
FATAL = ("undefined name", "undefined local", "syntax error",
         "redefinition of unused")


def test_no_module_uses_a_name_it_never_imports():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes not installed — pip install pyflakes")

    files = sorted(str(p) for p in ROOT.glob("*.py"))
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         capture_output=True, text=True).stdout
    bad = [line for line in out.splitlines()
           if any(f in line.lower() for f in FATAL)]
    assert not bad, "\n".join(["names used but never defined:"] + bad)
