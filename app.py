"""Web dashboard: scans the market on a background loop and serves the
latest results as a self-refreshing page. Entry point for deployment
(Railway, or any host that runs `gunicorn app:app`)."""

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

from flask import Flask

from options_scanner.config import ScreenerConfig
from options_scanner.market_hours import is_market_open
from options_scanner.report import render_html
from options_scanner.scanner import scan_universe
from options_scanner.universe import resolve_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

_lock = threading.Lock()
_state = {
    "results": [],
    "updated_at": None,
    "scanning": True,
    "error": None,
    "cycle_seconds": None,
}


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _build_config() -> ScreenerConfig:
    option_type = os.environ.get("OPTION_TYPE", "both")
    option_types = ("call", "put") if option_type == "both" else (option_type,)
    max_theta = _env_float("MAX_THETA_PCT", 0.05)
    return ScreenerConfig(
        option_types=option_types,
        min_dte=_env_int("MIN_DTE", 7),
        max_dte=_env_int("MAX_DTE", 45),
        min_volume=_env_int("MIN_VOLUME", 100),
        min_open_interest=_env_int("MIN_OPEN_INTEREST", 500),
        max_bid_ask_spread_pct=_env_float("MAX_SPREAD_PCT", 0.10),
        iv_min=_env_float("IV_MIN", 0.15),
        iv_max=_env_float("IV_MAX", 1.00),
        delta_min=_env_float("DELTA_MIN", 0.30),
        delta_max=_env_float("DELTA_MAX", 0.70),
        max_theta_pct_of_price=None if max_theta < 0 else max_theta,
        risk_free_rate=_env_float("RISK_FREE_RATE", 0.045),
    )


def _scan_loop() -> None:
    universe_spec = os.environ.get("UNIVERSE", "sp500")
    top_n = _env_int("TOP_N", 50)
    max_workers = _env_int("MAX_WORKERS", 8)
    request_delay = _env_float("REQUEST_DELAY", 0.25)
    # Floor between the *start* of one cycle and the next. 0 means: start
    # rescanning again immediately after a cycle finishes (fastest possible,
    # but hammers the free Yahoo Finance API - raise this if you get blocked).
    min_cycle_seconds = _env_float("MIN_CYCLE_SECONDS", 0)
    max_tickers = os.environ.get("MAX_TICKERS")

    cfg = _build_config()
    tickers = None
    # Floor applied only after a failed cycle, so a persistently broken
    # network (or a resolve_universe failure) can't spin in a tight loop.
    error_retry_floor_seconds = 5.0
    shutdown_outside_market_hours = os.environ.get("SHUTDOWN_OUTSIDE_MARKET_HOURS", "true").lower() != "false"

    while True:
        if shutdown_outside_market_hours and not is_market_open():
            logger.info("market is closed - shutting the process down (Railway's cron will restart it)")
            sys.stdout.flush()
            os._exit(0)

        cycle_start = time.monotonic()
        had_error = False
        try:
            if tickers is None:
                tickers = resolve_universe(universe_spec)
                if max_tickers:
                    tickers = tickers[: int(max_tickers)]
                logger.info("resolved %d tickers for universe %r", len(tickers), universe_spec)

            results = scan_universe(tickers, cfg, max_workers=max_workers, request_delay=request_delay)
            results.sort(key=lambda c: c.volume, reverse=True)
            with _lock:
                _state["results"] = results[:top_n]
                _state["updated_at"] = datetime.now(timezone.utc)
                _state["error"] = None
        except Exception as exc:
            had_error = True
            logger.exception("scan cycle failed")
            with _lock:
                _state["error"] = str(exc)
        finally:
            with _lock:
                _state["scanning"] = False

        elapsed = time.monotonic() - cycle_start
        with _lock:
            _state["cycle_seconds"] = round(elapsed, 1)
        floor = error_retry_floor_seconds if had_error else min_cycle_seconds
        sleep_for = max(0.0, floor - elapsed)
        if sleep_for:
            time.sleep(sleep_for)


_thread_started = False
_thread_lock = threading.Lock()


def start_background_scan() -> None:
    global _thread_started
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True
    threading.Thread(target=_scan_loop, daemon=True, name="scan-loop").start()


start_background_scan()


@app.route("/")
def index():
    with _lock:
        results = list(_state["results"])
        updated_at = _state["updated_at"]
        scanning = _state["scanning"]
        error = _state["error"]
        cycle_seconds = _state["cycle_seconds"]

    if not results and scanning:
        return (
            "<html><head><meta http-equiv='refresh' content='10'>"
            "<title>Options Scan Results</title></head>"
            "<body style='font-family:sans-serif;padding:2rem'>"
            "<h2>Scanning the market for the first time...</h2>"
            "<p>This page refreshes automatically every 10 seconds.</p>"
            "</body></html>"
        )

    page = render_html(results)
    page = page.replace("<head>", "<head><meta http-equiv='refresh' content='60'>", 1)

    status_bits = []
    if updated_at:
        status_bits.append(f"Last updated: {updated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if cycle_seconds is not None:
        status_bits.append(f"scan took {cycle_seconds}s")
    if status_bits:
        page = page.replace(
            "<h1>Options Scan Results</h1>",
            "<h1>Options Scan Results</h1>"
            f"<div style='font-size:0.8rem;opacity:0.7'>{' &middot; '.join(status_bits)}</div>",
            1,
        )

    if error:
        banner = (
            "<div style='background:#fee2e2;color:#7f1d1d;padding:0.6rem 1rem;"
            f"font-family:sans-serif;font-size:0.85rem'>Last scan error: {error}"
            " (showing the most recent successful results)</div>"
        )
        page = page.replace("<body>", f"<body>{banner}", 1)

    return page


@app.route("/health")
def health():
    with _lock:
        return {
            "status": "ok",
            "scanning": _state["scanning"],
            "results_count": len(_state["results"]),
            "updated_at": _state["updated_at"].isoformat() if _state["updated_at"] else None,
            "error": _state["error"],
        }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # A single-process server on purpose: the background scan loop calls
    # os._exit() to shut the whole container down outside market hours, and
    # that only works cleanly without a multi-worker manager (like gunicorn)
    # that would otherwise just respawn a new worker.
    from waitress import serve

    serve(app, host="0.0.0.0", port=port)
