"""Daily alert counter, guarded by a file lock.

scanner.py (every 30 min) and monitor.py (every 5 min) both increment the same
counter. Without a lock a scan and a monitor run that overlap can each read
alerts_sent=4, both send, and the 5/day cap silently becomes 6.
"""
import fcntl
import json
import datetime
from contextlib import contextmanager

import config as C


def _today():
    return datetime.date.today().isoformat()


def _fresh():
    return {"date": _today(), "alerts_sent": 0, "alerted_tickers": []}


def read():
    if C.STATE_FILE.exists():
        try:
            s = json.loads(C.STATE_FILE.read_text())
            if s.get("date") == _today():
                s.setdefault("alerts_sent", 0)
                s.setdefault("alerted_tickers", [])
                return s
        except (ValueError, OSError):
            pass
    return _fresh()


def write(s):
    C.STATE_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False))


@contextmanager
def locked():
    """with state.locked() as s: ... mutate s ...  (written back on exit)"""
    C.LOCK_FILE.touch(exist_ok=True)
    with open(C.LOCK_FILE, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            s = read()
            yield s
            write(s)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def capacity_left():
    return max(0, C.MAX_ALERTS_PER_DAY - read().get("alerts_sent", 0))


def record_alert(ticker):
    """Reserve one slot atomically. Returns True if the alert may be sent."""
    with locked() as s:
        if s["alerts_sent"] >= C.MAX_ALERTS_PER_DAY:
            return False
        if ticker in s["alerted_tickers"]:
            return False
        s["alerts_sent"] += 1
        s["alerted_tickers"].append(ticker)
        return True


def release_alert(ticker):
    """Give the slot back when the send failed."""
    with locked() as s:
        if s["alerts_sent"] > 0:
            s["alerts_sent"] -= 1
        if ticker in s["alerted_tickers"]:
            s["alerted_tickers"].remove(ticker)
