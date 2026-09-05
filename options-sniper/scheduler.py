"""Always-on scheduler — the entrypoint Railway runs.

One resident process dispatches scanner and monitor on their own intervals,
so both fire within seconds of their slot rather than whenever a external
scheduler gets around to it. That precision is the whole point for a
15-minute breakout.

Cost note: between runs the process sleeps, so measured CPU is near zero.
Railway bills actual usage, not the allocation.
"""
import datetime
import signal
import sys
import time
import traceback

import venv_boot

venv_boot.ensure(["requests"])

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


def park(problem):
    """Stay alive, doing nothing, until the operator fixes the configuration.

    Exiting non-zero here is the tidier signal, but it strands the operator:
    Railway's Console attaches to a *running* container, so a crash-looped
    service answers every diagnostic command with "container is not running"
    and the real cause is only visible in the deploy log. Parking keeps the
    shell reachable so `python telegram_send.py` and the rest can be run to
    find out why. Nothing is scanned and no alert is sent while parked, and
    the reason is reprinted every 5 minutes so it cannot scroll away.
    """
    log(f"NOT RUNNING: {problem}")
    log("Fix it in the service variables, then redeploy. The container stays "
        "up so the Console works — no scanning and no alerts until then.")
    while not _stop:
        for _ in range(300):
            if _stop:
                break
            time.sleep(1)
        if not _stop:
            log(f"still parked: {problem}")
    return 0


def main():
    log(f"scheduler up — scan every {C.SCAN_EVERY_MIN}m, "
        f"monitor every {C.MONITOR_EVERY_MIN}m, data in {C.DATA_DIR}")
    missing = [n for n, v in (("UW_API_KEY", C.UW_API_KEY),
                              ("TELEGRAM_BOT_TOKEN", C.TELEGRAM_TOKEN),
                              ("TELEGRAM_CHAT_ID", C.TELEGRAM_CHAT_ID)) if not v]
    if missing:
        return park(f"{', '.join(missing)} not set (or still holding the "
                    f".env.example placeholder)")

    last_scan = last_monitor = last_beat = None
    was_open = None

    while not _stop:
        now = market.now_et()
        is_open = market.is_open(now)

        if is_open != was_open:
            log("market OPEN" if is_open else f"market closed — {market.reason()}")
            was_open = is_open

        # Heartbeat. Without it the log is silent from Friday's close until
        # Monday's open, and there is no way to tell a healthy idle service
        # from a dead one. Hourly is quiet enough to stay readable.
        beat = slot(now, C.HEARTBEAT_MIN)
        if beat != last_beat:
            last_beat = beat
            if not is_open:
                log(f"alive, waiting — {market.reason()}")
            else:
                left = state.capacity_left()
                log(f"alive, market open — {left}/{C.MAX_ALERTS_PER_DAY} alerts left today")

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
