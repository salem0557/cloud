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
    return {"date": _today(), "alerts_sent": 0, "alerted_tickers": [],
            "watched_tickers": []}


def read():
    if C.STATE_FILE.exists():
        try:
            s = json.loads(C.STATE_FILE.read_text())
            if s.get("date") == _today():
                s.setdefault("alerts_sent", 0)
                s.setdefault("alerted_tickers", [])
                s.setdefault("watched_tickers", [])
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


def record_watch(ticker):
    """Reserve the one heads-up a ticker gets per day. -> True if it may be sent.

    The flag used to live on the shortlist row, and the scanner rebuilds that
    file from scratch every 10 minutes without carrying anything over — so the
    flag was wiped and the next monitor pass sent the same notice again. HWM
    went out twice on 2026-09-08, at 17:01:15 and 17:05:46, one monitor beat
    either side of a scan. This lives in the day's state, which nothing
    rewrites, and it resets with the date like every other daily counter.
    """
    with locked() as s:
        if ticker in s["watched_tickers"]:
            return False
        if (C.MAX_WATCH_PER_DAY
                and len(s["watched_tickers"]) >= C.MAX_WATCH_PER_DAY):
            return False        # the good one has to stay findable
        s["watched_tickers"].append(ticker)
        return True


def release_watch(ticker):
    """Give the reservation back when the send did not actually go out."""
    with locked() as s:
        if ticker in s["watched_tickers"]:
            s["watched_tickers"].remove(ticker)


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
