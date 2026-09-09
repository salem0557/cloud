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
            "watched_tickers": [], "alerted": {}}


def read():
    if C.STATE_FILE.exists():
        try:
            s = json.loads(C.STATE_FILE.read_text())
            if s.get("date") == _today():
                s.setdefault("alerts_sent", 0)
                s.setdefault("alerted_tickers", [])
                s.setdefault("watched_tickers", [])
                s.setdefault("alerted", {})
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


def record_alert(ticker, level=None, direction=None, atr=None):
    """Reserve one slot atomically. Returns True if the alert may be sent.

    A name alerts once a day unless the new setup is genuinely a different and
    stronger one — see REALERT in config.py. `level`, `direction` and `atr`
    describe the break being alerted; without them the old one-a-day rule
    applies, so any caller that has not been taught the new arguments keeps the
    safe behaviour rather than the permissive one.
    """
    with locked() as s:
        if s["alerts_sent"] >= C.MAX_ALERTS_PER_DAY:
            return False
        seen = s.setdefault("alerted", {})
        prev = seen.get(ticker)
        if prev and not _stronger(prev, level, direction, atr):
            return False
        if prev and len(prev.get("levels", [])) >= C.MAX_ALERTS_PER_TICKER:
            return False
        s["alerts_sent"] += 1
        if ticker not in s["alerted_tickers"]:
            s["alerted_tickers"].append(ticker)
        entry = prev or {"levels": [], "direction": direction}
        entry["levels"] = entry.get("levels", []) + [level]
        entry["direction"] = direction
        entry["at"] = datetime.datetime.now().isoformat(timespec="seconds")
        seen[ticker] = entry
        return True


def _stronger(prev, level, direction, atr):
    """Is this a NEW break, or the same one arriving again?"""
    if not C.REALERT or level is None or direction is None:
        return False
    # A flip is a different trade, whatever the level or the clock says.
    if prev.get("direction") and prev["direction"] != direction:
        return True
    try:
        last = datetime.datetime.fromisoformat(prev["at"])
    except (KeyError, ValueError, TypeError):
        return False
    if (datetime.datetime.now() - last).total_seconds() / 60 < C.REALERT_COOLDOWN_MIN:
        return False
    prev_levels = [x for x in prev.get("levels", []) if x is not None]
    if not prev_levels or not atr:
        return False
    margin = C.REALERT_LEVEL_ATR * atr
    # Beyond the HIGHEST level already alerted for a call, the lowest for a put.
    return (level >= max(prev_levels) + margin if direction == "call"
            else level <= min(prev_levels) - margin)


def release_alert(ticker):
    """Give the slot back when the send failed."""
    with locked() as s:
        if s["alerts_sent"] > 0:
            s["alerts_sent"] -= 1
        if ticker in s["alerted_tickers"]:
            s["alerted_tickers"].remove(ticker)
