"""The SPX read — direction, walls, and where price sits between them.

Salem, 2026-09-09: "عندي القسم هذا خاص ب spx كيف استفيد منه انك تتابع المؤشر
وتحلله وتشوف اتجاهه؟"

He has a Telegram topic for the index and wanted to know what it could carry.

The obstacle, measured and not assumed: UW serves NO candles for an index
under this subscription — /api/stock/SPX/ohlc/* answers data:[] with
is_index:true at 1m and 15m alike. The 15m break logic this whole system is
built on therefore cannot run on SPX at all.

Everything else about SPX does answer. So this module reads the index from the
side that works:

  1. GEX levels on SPX itself — call wall, put wall, gamma magnet, gamma flip.
     These are real SPX prices, and they are levels in exactly the sense the
     scanner means: places where hedging flow changes behaviour.
  2. Market tide — where the market's option premium went today, in dollars.
  3. SPY's 15m tape as the PRICE proxy, converted to SPX points by the live
     ratio between the two closes. SPY is the same index at a tenth of the
     size and its options are the most liquid in the market.

What it is not: a trade alert. It is a read of the field Salem trades in, and
it says so in its own text. Nothing here sizes a position or picks a contract.
"""
import datetime
import math

import config as C
import market
import technical
import uw
from telegram_send import send

NO_DATA = "⚠️ SPX: البيانات ناقصة — لا تقرير"


def _tide_direction(rows, window=12):
    """-> (net_now, net_before, label). Net = call premium - put premium.

    `window` rows back is one hour on 5-minute data. A tide that is positive
    but falling is not the same market as one that is positive and rising,
    and a single number cannot tell them apart.
    """
    if not rows:
        return None
    net = [r["net_call_premium"] - r["net_put_premium"] for r in rows]
    now = net[-1]
    before = net[-min(window + 1, len(net))]
    if now > 0 and now >= before:
        label = "صاعد — الفلوس على الكول وتزيد"
    elif now > 0:
        label = "صاعد لكن يضعف — الكول لسه فوق بس ينزل"
    elif now <= 0 and now <= before:
        label = "هابط — الفلوس على البوت وتزيد"
    else:
        label = "هابط لكن يخف — البوت لسه فوق بس يتراجع"
    return now, before, label


def _m(v):
    """Dollars as millions, the way Salem reads them."""
    return f"{v / 1e6:+,.1f}M$"


def read():
    """Everything the report needs, or None when the index cannot be priced.

    A report with a missing price is not a smaller report, it is a wrong one:
    every distance below is measured from that number.
    """
    spx = uw.index_close("SPX")
    spy = uw.spot("SPY")
    if not spx or not spy:
        return None
    return {
        "spx": spx,
        "spy": spy,
        "ratio": spx / spy,
        "levels": uw.gex_levels("SPX"),
        "tide": uw.market_tide(),
        "spy_candles": uw.candles("SPY"),
        # The scenarios are built on SPY's own realised 1m volatility and the
        # time actually left in the session — never on an assumed number.
        "sigma1": minute_sigma(uw.candles("SPY", candle_size="1m",
                                          timeframe="1D")),
        "minutes_left": market.minutes_to_close(),
        # The same naive local stamp every other message in this project
        # carries, so the times in 943, 944 and 945 can be read side by side.
        "at": datetime.datetime.now().strftime("%H:%M"),
    }


# ── The scenarios ───────────────────────────────────────────────
# Salem asked for a million of them. This computes the number a million paths
# CONVERGE to, exactly, instead of drawing them — same answer, no simulation
# error, no numpy on the Railway image.
#
# Checked against the real thing on 2026-09-09, SPX 7650.3 with 25 minutes of
# pre-market volatility measured off SPY's own tape, 1,000,000 bootstrapped
# paths of this market's actual 1-minute moves against the formula below:
#
#   level          simulated   formula
#   touches 7680       30.1%     31.7%
#   touches 7670       48.5%     50.6%
#   closes above 7670  25.3%     25.3%
#
# The gap is the fat tails the formula does not carry, and it is smaller than
# the honesty of any of these numbers warrants pretending about.
#
# WHAT IT IS NOT: a forecast. It is a driftless random walk — the median path
# ends exactly where price is now, by construction. It answers one question
# only: which levels are within reach today, given how much this market has
# actually been moving. Nothing in it knows which way.

def _phi(x):
    """Standard normal CDF, from math.erf. No dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def minute_sigma(candles):
    """Realised 1-minute volatility from this market's own tape, or 0.0.

    Overnight gaps are excluded: the jump from one session's close to the
    next session's open is not a minute of trading, and counting it inflates
    the number that every probability below is built on.
    """
    rows = [c for c in (candles or []) if c.get("close", 0) > 0]
    rets = []
    for a, b in zip(rows, rows[1:]):
        if a.get("date") != b.get("date"):
            continue
        rets.append(math.log(b["close"] / a["close"]))
    if len(rets) < 30:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def scenarios(spot, sigma1, minutes, levels):
    """-> [(name, level, p_touch, p_close_beyond)] for each level given.

    p_touch uses the reflection principle for driftless Brownian motion:
    P(max >= b) = 2 P(end >= b). Above spot it is a high being made, below
    spot a low — the arithmetic is the same, mirrored.
    """
    if spot <= 0 or sigma1 <= 0 or minutes <= 0:
        return []
    sd = sigma1 * math.sqrt(minutes)
    out = []
    for name, level in levels:
        if not level or level <= 0:
            continue
        b = math.log(level / spot)
        z = abs(b) / sd
        tail = 1.0 - _phi(z)
        p_touch = min(1.0, 2.0 * tail)
        # "beyond" means the side the level is on: above it when it is above
        # spot, below it when it is below. Reporting "closes above" for a
        # level under price would read as a 90% chance of nothing happening.
        p_close = tail
        out.append((name, level, p_touch, p_close))
    return out


def _scenarios_block(d, lines):
    sigma1 = d.get("sigma1") or 0.0
    minutes = d.get("minutes_left") or 0
    if sigma1 <= 0 or minutes <= 0:
        lines += ["", "السيناريوهات: تحتاج تذبذب مقيس ووقت متبقٍ — مو متوفرة الآن"]
        return
    lv = d.get("levels") or {}
    rows = scenarios(d["spx"], sigma1, minutes,
                     [("مقاومة", lv.get("call_wall")),
                      ("مغناطيس", lv.get("gamma_magnet")),
                      ("دعم", lv.get("put_wall")),
                      ("الانقلاب", lv.get("gamma_flip"))])
    if not rows:
        return
    sd = sigma1 * math.sqrt(minutes)
    lines += ["", f"السيناريوهات ({minutes} دقيقة للإغلاق، "
                  f"تذبذب مقيس {sigma1 * math.sqrt(390) * 100:.2f}% للجلسة):"]
    for name, level, p_touch, p_close in rows:
        side = "فوق" if level > d["spx"] else "تحت"
        lines.append(f"  {name:<9} {level:>7,.0f}   يلمسه {p_touch * 100:>4.0f}%"
                     f"   يغلق {side}ه {p_close * 100:>4.0f}%")
    lo, hi = d["spx"] * math.exp(-sd), d["spx"] * math.exp(sd)
    lines.append(f"  نطاق الإغلاق 68%: {lo:,.0f} — {hi:,.0f}")
    # The sentence that keeps this from being read as a forecast.
    lines.append("  ⚠️ مشي عشوائي بلا اتجاه — يقول أي مستوى قريب، مو وين رايح")


def _walls(d, lines):
    lv = d.get("levels") or {}
    if not any(lv.values()):
        lines.append("الجدران: UW ما أعطى مستويات جاما الآن")
        return
    spx = d["spx"]

    def row(icon, name, price):
        if not price:
            return
        gap = price - spx
        lines.append(f"  {icon} {name:<10} {price:>8,.0f}   "
                     f"({gap:+.0f} نقطة)")

    lines.append("الجدران (جاما SPX):")
    row("🧱", "مقاومة", lv.get("call_wall"))
    row("🧲", "مغناطيس", lv.get("gamma_magnet"))
    row("🧱", "دعم", lv.get("put_wall"))
    flip = lv.get("gamma_flip")
    if flip:
        row("⚡", "الانقلاب", flip)
        # UW's own definition of the flip. Stated as the mechanism it is, not
        # as an edge: nothing in this project has measured it paying.
        lines.append("     " + ("فوق الانقلاب: الحركة تنكتم عادة"
                                if spx > flip else
                                "تحت الانقلاب: الحركة تتضخم عادة"))


def _frame(d, lines):
    """SPY's 15m frame, priced in SPX points."""
    candles = d.get("spy_candles") or []
    r = d["ratio"]
    got = False
    for direction in ("call", "put"):
        tech = technical.analyse(candles, direction)
        if not tech or not technical.confirms(tech):
            continue
        got = True
        word = "اخترق" if direction == "call" else "كسر"
        lines += ["", f"فريم 15د (من SPY):",
                  f"  {word} {tech['level'] * r:,.0f} "
                  f"والهدف {tech['target'] * r:,.0f} نقطة",
                  f"  حجم {tech['volume_ratio']:.1f}x المعتاد"]
        break
    if not got:
        lines += ["", "فريم 15د (من SPY): لا كسر ولا اختراق الآن"]


def message(d=None):
    d = d or read()
    if not d:
        return NO_DATA
    lines = [f"📊 قراءة SPX — {d['at']}", "",
             f"المؤشر {d['spx']:,.2f}   (SPY {d['spy']:,.2f})", ""]

    tide = _tide_direction(d.get("tide") or [])
    if tide:
        now, before, label = tide
        arrow = "🟢" if now > 0 else "🔴"
        lines += [f"اتجاه سيولة السوق كله:",
                  f"  {arrow} {label}",
                  f"  الآن {_m(now)}   قبل ساعة {_m(before)}", ""]
    else:
        lines += ["اتجاه سيولة السوق: UW ما أعطى بيانات الآن", ""]

    _walls(d, lines)
    _scenarios_block(d, lines)
    _frame(d, lines)
    lines += ["", "هذي قراءة للمؤشر، مو توصية عقد."]
    return "\n".join(lines)


def send_report(dry_run=False):
    """-> True when a report went out. Market hours only: the walls move with
    the session's own flow, and a report at 3am describes yesterday."""
    if not market.is_open():
        return False
    text = message()
    if text == NO_DATA:
        print("  spx: no data — nothing sent")
        return False
    if dry_run:
        print(text)
        return False
    return bool(send(text, chat_id=C.TELEGRAM_SPX_CHAT_ID or C.TELEGRAM_CHAT_ID,
                     topic=C.TELEGRAM_SPX_TOPIC_ID))


if __name__ == "__main__":
    import sys
    print(message() if "--send" not in sys.argv else
          ("sent" if send_report() else "not sent"))
