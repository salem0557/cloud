"""Always-on scheduler for a single long-lived host (Railway, Fly, a VPS).

Why a loop instead of cron: GitHub's scheduled workflows are best-effort and
commonly fire 5-20 minutes late, which is useless for a 15-minute breakout.
This process stays resident and fires on the minute, so the 5-minute monitor
cadence is real rather than nominal.

Cost note: between runs the process sleeps, so measured CPU is near zero.
Railway bills actual usage, not the allocation.
"""
import datetime
import signal
import sys
import time
import traceback

import config as C
import market
import monitor
import scanner

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    print(f"[scheduler] signal {signum} — finishing the current tick and exiting",
          flush=True)
    _stop = True


signal.signal(signal.SIGTERM, _handle_signal)   # Railway sends SIGTERM on redeploy
signal.signal(signal.SIGINT, _handle_signal)


def log(msg):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def slot(now, every_min):
    """A stable key for the current N-minute window, so a tick that runs twice
    inside the same minute cannot fire the same job twice."""
    minutes = now.hour * 60 + now.minute
    return (now.date(), minutes // every_min)


def run(name, fn):
    started = time.monotonic()
    try:
        fn()
    except Exception:
        # one failing scan must never take the scheduler down with it
        log(f"{name} FAILED:\n{traceback.format_exc()}")
    else:
        log(f"{name} finished in {time.monotonic() - started:.1f}s")


def main():
    log(f"scheduler up — scan every {C.SCAN_EVERY_MIN}m, "
        f"monitor every {C.MONITOR_EVERY_MIN}m, data in {C.DATA_DIR}")
    if not C.UW_API_KEY:
        log("FATAL: UW_API_KEY is empty — set it in the host's variables")
        return 1

    last_scan = last_monitor = None
    was_open = None

    while not _stop:
        now = market.now_et()
        is_open = market.is_open(now)

        if is_open != was_open:
            log("market OPEN" if is_open else f"market closed — {market.reason()}")
            was_open = is_open

        if is_open:
            s = slot(now, C.SCAN_EVERY_MIN)
            if s != last_scan:
                last_scan = s
                log("running scanner")
                run("scanner", scanner.main)

            m = slot(now, C.MONITOR_EVERY_MIN)
            if m != last_monitor:
                last_monitor = m
                run("monitor", monitor.main)

        # 20s keeps firing within a few seconds of the minute while staying
        # cheap; the slot key stops a job repeating inside its own window.
        for _ in range(20):
            if _stop:
                break
            time.sleep(1)

    log("scheduler stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
