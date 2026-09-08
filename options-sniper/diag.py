"""Why did the scan score nothing? Run: python diag.py [TICKER]

The log said: 60 tickers worth a data call, 60 candle requests, ZERO option
chain requests, an empty shortlist and no alerts. Zero chain requests is the
tell — every ticker was dropped before evaluate() ever priced a contract, and
every one of those exits is silent. This walks one ticker through the same
calls the scanner makes and prints what each one actually returned.
"""
import sys

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import technical
import uw

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "META").upper()
print(f"=== {TICKER} — the exact path scanner.evaluate() takes ===\n")

# 1. the raw request, with the error NOT swallowed
print(f"1. GET /api/stock/{TICKER}/ohlc/{C.CANDLE_SIZE}?timeframe=5D&limit=500")
try:
    raw = uw._get(f"/api/stock/{TICKER}/ohlc/{C.CANDLE_SIZE}",
                  {"timeframe": "5D", "limit": 500})
    print(f"   -> {len(raw)} raw rows")
    if raw:
        k = raw[0]
        print(f"   -> fields on row 0: {sorted(k.keys())}")
        print(f"   -> row 0: {k}")
except uw.UWError as e:
    print(f"   -> UWError: {e}")
    print("\n   This is the answer: uw.candles() catches this and returns [],")
    print("   so every ticker looked like 'no data' instead of 'request failed'.")
    sys.exit(0)
except Exception as e:
    print(f"   -> {type(e).__name__}: {e}")
    sys.exit(0)

# 2. the same rows after uw.candles() has parsed and filtered them
rows = uw.candles(TICKER, timeframe="5D")
print(f"\n2. uw.candles() kept {len(rows)} of {len(raw)}  "
      f"(needs >= {C.CANDLES_LOOKBACK} for technical.analyse)")
if len(rows) < len(raw):
    kept_closed = sum(1 for c in raw
                      if uw._is_closed(c.get("end_time", "")))
    has_end = sum(1 for c in raw if c.get("end_time"))
    has_close = sum(1 for c in raw if uw._num(c.get("close")) > 0)
    reg = sum(1 for c in raw if c.get("market_time") in (None, "r"))
    print(f"   rows carrying end_time      : {has_end}/{len(raw)}")
    print(f"   rows _is_closed() accepts   : {kept_closed}/{len(raw)}")
    print(f"   rows with close > 0         : {has_close}/{len(raw)}")
    print(f"   rows passing market_time    : {reg}/{len(raw)}")
    print("   ^ whichever of these is 0 or tiny is the filter that emptied it")

if not rows:
    print("\n   -> technical.analyse() gets nothing, returns None, and the")
    print("      ticker is skipped with no message. That is the whole bug.")
    sys.exit(0)

# 3. the measurement
t = technical.analyse(rows, "call")
if t is None:
    print(f"\n3. technical.analyse() -> None with {len(rows)} bars")
    print(f"   needs >= {max(C.CANDLES_LOOKBACK, C.ATR_PERIOD + 2)} bars AND atr > 0")
else:
    print(f"\n3. technical.analyse() -> level {t['level']}  atr {t['atr']}  "
          f"vol {t['volume_ratio']:.2f}x  broke {t['broke_level']}  "
          f"room {t['remaining_atr']:+.2f}")
    print(f"   confirms() = {technical.confirms(t)}")

# 4. the stage that never ran
print(f"\n4. GET the option chain")
try:
    chain = uw.option_chain(TICKER)
    print(f"   -> {len(chain)} contracts, {sum(1 for c in chain if c.get('ask'))} priced")
except uw.UWError as e:
    print(f"   -> UWError: {e}")

print("\n" + uw.spent())
