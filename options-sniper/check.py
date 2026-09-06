"""Pre-flight check — run `python check.py` in the host's shell.

Exists because the numbers this system alerts on depend on four external
surfaces, and a subscription tier that silently omits one of them looks
exactly like a working install until the market opens and nothing arrives.
The candle check is the one that matters most: without 15m candles the
technical component is always 0, which caps the total at 70 against an 85
threshold — no alert can ever fire.

Also a paste workaround: Railway's web console mangles multi-line pastes
(bracketed-paste escapes arrive as literal text), so a diagnostic that has to
be pasted cannot be run there. This one is typed in full as `python check.py`.
"""
import os
import sys

import venv_boot

venv_boot.ensure(["requests"])

import config as C

OK, WARN, BAD = "PASS", "WARN", "FAIL"
results = []


def report(name, status, detail=""):
    results.append((name, status))
    mark = {OK: "[ PASS ]", WARN: "[ WARN ]", BAD: "[ FAIL ]"}[status]
    print(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def section(title):
    print(f"\n{'─' * 60}\n{title}\n{'─' * 60}")


# ── 1. Credentials ──────────────────────────────────────────────
section("1. Credentials")
for name, value in (("UW_API_KEY", C.UW_API_KEY),
                    ("TELEGRAM_BOT_TOKEN", C.TELEGRAM_TOKEN),
                    ("TELEGRAM_CHAT_ID", C.TELEGRAM_CHAT_ID)):
    report(name, OK if value else BAD,
           f"{value[:6]}…" if value else "empty, or still the .env.example placeholder")
report("FINVIZ_AUTH", OK if C.FINVIZ_AUTH else WARN,
       f"{C.FINVIZ_AUTH[:6]}…" if C.FINVIZ_AUTH else "not set — scans run UW-only")

# ── 2. Dependencies ─────────────────────────────────────────────
# Two of these are imported lazily, deep inside a code path, so a missing one
# surfaces only when Salem runs the backtest or turns the analyst on — long
# after the deploy that dropped it. Checked up front instead.
section("2. Dependencies")
for mod, need, why in (("requests", True, "live scanning"),
                       ("anthropic", False, "analyst layer — USE_ANALYST=1")):
    try:
        __import__(mod)
        report(mod, OK, why)
    except ImportError:
        report(mod, BAD if need else WARN,
               f"missing — {why} unavailable. Fix: pip install {mod}")


# ── 3. Storage ──────────────────────────────────────────────────
section("3. Storage")
if str(C.DATA_DIR) == "/data":
    report("volume mounted", OK, str(C.DATA_DIR))
elif str(C.DATA_DIR).startswith("/app"):
    report("volume mounted", BAD,
           f"{C.DATA_DIR} is ephemeral — every redeploy wipes state.json and journal.csv")
else:
    report("data directory", WARN, str(C.DATA_DIR))
try:
    probe = C.DATA_DIR / ".write-probe"
    probe.write_text("ok")
    probe.unlink()
    report("writable", OK)
except OSError as e:
    report("writable", BAD, str(e))

# ── 3. Unusual Whales ───────────────────────────────────────────
import technical  # noqa: E402
import uw  # noqa: E402

section("4. Unusual Whales")


def why(path, params=None):
    """uw.candles()/option_chain() swallow errors and return [] so a live scan
    degrades instead of crashing. For a diagnostic that hides the cause, so
    re-issue the request raw to report what UW actually said."""
    try:
        uw._get(path, params)
        return "endpoint responded but returned nothing"
    except uw.UWError as e:
        return str(e)


if not C.UW_API_KEY:
    report("all UW checks", BAD, "skipped — UW_API_KEY is not set")
    candles = []
else:
  try:
      alerts = uw.flow_alerts()
      if alerts:
          a = alerts[0]
          report("flow alerts", OK,
                 f"{len(alerts)} rows — e.g. {a['ticker']} {a['type']} "
                 f"${a['total_premium']:,.0f}")
      else:
          report("flow alerts", WARN, "0 rows — normal while the market is closed")
  except Exception as e:
      report("flow alerts", BAD, str(e))

  candles = []
  try:
      candles = uw.candles("AAPL", timeframe="5D")
      if len(candles) >= C.CANDLES_LOOKBACK:
          last = candles[-1]
          report("15m candles", OK,
                 f"{len(candles)} bars — last {last['end_time']} close {last['close']}")
      elif candles:
          report("15m candles", WARN,
                 f"only {len(candles)} bars, need {C.CANDLES_LOOKBACK}")
      else:
          report("15m candles", BAD,
                 f"0 bars ({why(f'/api/stock/AAPL/ohlc/{C.CANDLE_SIZE}', {'timeframe': '5D'})}). "
                 "Technical score is stuck at 0, so no total can reach "
                 f"{C.THRESHOLD} — NO ALERT WILL EVER FIRE")
  except Exception as e:
      report("15m candles", BAD, str(e))

  if candles:
      t = technical.analyse(candles, "call")
      if t:
          report("technical analysis", OK,
                 f"level {t['level']} | ATR {t['atr']} | vol {t['volume_ratio']}x")
      else:
          report("technical analysis", BAD, "not enough usable bars")

  try:
      chain = uw.option_chain("AAPL")
      priced = [c for c in chain if c["ask"] > 0]
      greeked = [c for c in chain if c["delta"]]
      if not chain:
          report("option chain", BAD,
                   f"0 contracts ({why('/api/stock/AAPL/option-chains', {'greeks': 'true'})}) "
                   "— alerts would carry no contracts")
      elif not priced:
          report("option chain", BAD,
                 f"{len(chain)} contracts but none priced — the budget filter "
                 "(ask x 100) needs an ask")
      elif not greeked:
          report("option chain", WARN,
                 f"{len(chain)} contracts priced but no delta — profit estimates "
                 "will read 0%")
      else:
          c = chain[0]
          report("option chain", OK,
                 f"{len(chain)} contracts, {len(priced)} priced, {len(greeked)} with delta")
          print(f"         sample: {c['option_symbol']} {c['strike']}{c['type'][0].upper()} "
                f"bid {c['bid']} ask {c['ask']} delta {c['delta']} OI {c['open_interest']:.0f}")
  except Exception as e:
      report("option chain", BAD, str(e))

  # Daily bars carry no start/end time; they were silently dropped for months.
  try:
      t = uw.stock_technicals("AAPL")
      if t["atr"] > 0 and t["rsi"] is not None:
          report("daily technicals", OK,
                 f"ATR {t['atr']} | RSI {t['rsi']} | vs SMA20 {t['vs_sma20']}%")
      elif t["atr"] > 0:
          report("daily technicals", WARN,
                 f"ATR {t['atr']} but no RSI — fewer than 15 daily bars returned")
      else:
          report("daily technicals", BAD,
                 f"ATR 0 ({why('/api/stock/AAPL/ohlc/1d', {'timeframe': '6M'})}) "
                 "— atr_to_strike would be blank on every row")
  except Exception as e:
      report("daily technicals", BAD, str(e))

  # The frame the alerts are actually read on, and the one a 0DTE move fits in.
  try:
      t = uw.intraday_technicals("AAPL")
      if t["session_move"] > 0:
          report("15m technicals", OK,
                 f"{t['bars']} bars | ATR15 {t['atr15']} | RSI {t['rsi']} | "
                 f"session move ${t['session_move']}")
      else:
          report("15m technicals", BAD,
                 f"no usable 15m bars ({t['bars']} returned) — distance to "
                 "strike would fall back to the daily ATR")
  except Exception as e:
      report("15m technicals", BAD, str(e))

# ── 4. Finviz ───────────────────────────────────────────────────
section("5. Finviz (candidate discovery only)")
if not C.FINVIZ_AUTH:
    report("screener", WARN, "no token — skipped")
else:
    import finviz  # noqa: E402
    movers = finviz.movers()
    if movers:
        report("screener", OK,
               f"{len(movers)} movers — {', '.join(m['ticker'] for m in movers[:8])}")
    else:
        report("screener", WARN,
               "0 rows. Market closed, or the token is wrong — check the log "
               "line above for 'got HTML, not CSV'")

# ── Session ─────────────────────────────────────────────────────
import market as _mk  # noqa: E402
_now = _mk.now_et()
if _mk.is_open(_now):
    report("session", OK, f"open, {_mk.minutes_to_close(_now)} min to the bell")
else:
    report("session", WARN, _mk.reason())

# ── Paper book ──────────────────────────────────────────────────
section("6. Paper book")
import paper  # noqa: E402
st = paper.summary()
if st["n"] or st["open"]:
    report("record", OK,
           f"{st['n']} closed, {st['open']} open"
           + (f" — {st['hit']:.0f}% hit, ${st['avg']:.3f}/$1"
              if st["n"] else ""))
else:
    report("record", WARN, "empty — no alert has opened a paper position yet")
if C.TELEGRAM_PAPER_CHAT_ID:
    report("paper chat", OK, f"separate — {C.TELEGRAM_PAPER_CHAT_ID[:6]}…")
else:
    report("paper chat", WARN,
           "not set — results will share the alerts chat "
           "(set TELEGRAM_PAPER_CHAT_ID)")

# ── 5. Telegram ─────────────────────────────────────────────────
section("7. Telegram")
if "--no-telegram" in sys.argv:
    report("send", WARN, "skipped (--no-telegram)")
elif not (C.TELEGRAM_TOKEN and C.TELEGRAM_CHAT_ID):
    report("send", BAD, "credentials missing — cannot test")
else:
    from telegram_send import send  # noqa: E402
    report("send", OK if send("✅ اختبار: بوت التنبيهات يعمل") else BAD,
           "check your phone" )

# ── Verdict ─────────────────────────────────────────────────────
section("Verdict")
bad = [n for n, s in results if s == BAD]
warn = [n for n, s in results if s == WARN]
print(f"{len(results) - len(bad) - len(warn)} passed, {len(warn)} warnings, {len(bad)} failed")
if bad:
    print("\nBLOCKING: " + ", ".join(bad))
    print("The system will not deliver alerts until these are fixed.")
elif warn:
    print("\nUsable. Warnings are expected while the market is closed.")
else:
    print("\nAll green.")
sys.exit(1 if bad else 0)
