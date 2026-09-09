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
        # The same naive local stamp every other message in this project
        # carries, so the times in 943, 944 and 945 can be read side by side.
        "at": datetime.datetime.now().strftime("%H:%M"),
    }


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
