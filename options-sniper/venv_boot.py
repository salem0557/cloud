"""Re-exec under the interpreter that actually has the dependencies.

Railway's web console opens a plain shell, so `python` there is the system
interpreter while the service runs inside /opt/venv. Every dependency lives in
the venv, so a script started as `python backtest.py` dies on the first import
that matters — and the error names the package, which reads like a broken
install even right after pip reported success, because pip installed into the
venv and the script is not running in it.

check.py carried this logic privately and backtest.py did not, so the fix that
worked for one did nothing for the other. It lives here now, and every
entrypoint calls it.
"""
import os
import subprocess
import sys

CANDIDATES = ("/opt/venv/bin/python", "/opt/venv/bin/python3",
              "/app/.venv/bin/python", "/usr/local/bin/python3", "/usr/bin/python3")


def _has(python, modules):
    if python == sys.executable:
        return False
    if not os.path.exists(python):
        return False
    code = "import " + ", ".join(modules)
    try:
        return subprocess.run([python, "-c", code], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def missing(modules):
    out = []
    for m in modules:
        try:
            __import__(m)
        except ImportError:
            out.append(m)
    return out


def ensure(modules, hint=""):
    """Re-exec into an interpreter that can import all of `modules`.

    Returns normally when the current interpreter already can. Exits 2 with an
    actionable message when no interpreter on the box can — guessing further
    would only produce a longer traceback.
    """
    absent = missing(modules)
    if not absent:
        return
    for python in CANDIDATES:
        if _has(python, modules):
            print(f"(re-running under {python} — this shell's python is missing "
                  f"{', '.join(absent)})\n", flush=True)
            os.execv(python, [python] + sys.argv)
    print(f"FAIL: cannot import {', '.join(absent)}, and no interpreter on this "
          f"machine has them.\n"
          f"      Install into the venv the service uses:\n"
          f"        /opt/venv/bin/pip install {' '.join(absent)}\n"
          f"      then run with that same interpreter:\n"
          f"        /opt/venv/bin/python {os.path.basename(sys.argv[0])}"
          + (f"\n      {hint}" if hint else ""), file=sys.stderr)
    sys.exit(2)
