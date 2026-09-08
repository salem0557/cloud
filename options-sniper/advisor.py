"""Watch a position Salem is actually in, and say when to get out.

He asked to be moved from a sender of alerts to an adviser:

    "ان ارسلت لي تنبيه ... انا سأقتبس ردك واقول (اشتريت سترايك كذا) بعدها
     تتابع العقد الذي شريته ... وتخبرني ان حان وقت الخروج سواء الربح يكفي
     او هنالك خطر قادم. ايضا استطيع سؤالك (ابيع سترايك كذا) وتخبرني اصبر
     هنالك سيولة قادمة ام نعم ابيع"

That is buildable, and two words in it are not.

**"بالثانية".** UW serves ONE MINUTE bars. There is no per-second tape on this
plan, so the honest cadence is a minute, and in practice the scheduler's beat.
Saying "by the second" would be a promise the data cannot keep.

**"سيولة قادمة".** Nothing here sees the future. What it can see is money
arriving or leaving RIGHT NOW: the ask-side share of the last few minutes of
the contract's own tape, and the net premium sitting at that strike. "Buyers
are still lifting" is a fact. "Liquidity is coming" is a forecast, and this
file does not make forecasts.

So every line it produces is one of two things — a measured fact, or a rule
applied to measured facts — and the rules are the ones already measured in
this project rather than new ones invented for the occasion.
"""
import datetime

import config as C
import market
import technical
import uw

# How many recent minutes of the contract's own tape decide "are buyers still
# here". Short enough to be about now, long enough that one quiet minute is
# not a verdict.
PRESSURE_MINUTES = int(getattr(C, "ADVISOR_PRESSURE_MIN", 10) or 10)

# Ask-side share below this and buyers have stopped lifting; it is the same
# threshold the entry uses, so entry and exit do not disagree about what
# pressure means.
PRESSURE_FLOOR = C.MIN_ASK_SIDE_RATIO

# Minutes before the hard exit at which a same-day contract is called in
# regardless of anything else.
CLOSING_WARN_MIN = int(getattr(C, "ADVISOR_CLOSING_WARN_MIN", 20) or 20)


def _pct(now, entry):
    return ((now / entry) - 1.0) * 100 if (now and entry) else None


def contract_pressure(option_symbol, minutes=PRESSURE_MINUTES, date=None):
    """Ask-side share of the last `minutes` of this contract's tape.

    -> {"ask_share", "volume", "minutes"} or None when the tape is too thin
    to say anything. None means unknown, and unknown is never reported as
    calm.
    """
    try:
        rows = uw.contract_intraday(option_symbol, date=date)
    except uw.UWError:
        return None
    tail = [r for r in (rows or [])[-minutes:]]
    ask = sum((r.get("ask_volume") or 0) for r in tail)
    bid = sum((r.get("bid_volume") or 0) for r in tail)
    sided = ask + bid
    if sided < 20:                      # too few prints to read pressure from
        return None
    return {"ask_share": ask / sided, "volume": sided, "minutes": len(tail)}


def strike_net(ticker, strike, is_call, date=None):
    """Net premium sitting at that strike today. -> dollars, or None.

    Positive means more was lifted at the offer than hit at the bid — money
    building the position. Negative means it is being sold. It is a fact about
    today, not a prediction about the next hour.
    """
    try:
        rows = uw.strike_flow(ticker, date=date)
    except uw.UWError:
        return None
    for r in rows:
        if r["strike"] is not None and abs(r["strike"] - strike) < 0.001:
            return ((r["call_ask"] - r["call_bid"]) if is_call
                    else (r["put_ask"] - r["put_bid"]))
    return None


def read(pos, tech=None):
    """Everything measurable about one open position, right now."""
    sym = pos["option_symbol"]
    is_call = (pos.get("type") or pos.get("direction") or "call") == "call"
    q = uw.contract_quote(sym)
    now_px = (q or {}).get("price") or None
    facts = {
        "ticker": pos.get("ticker", ""), "strike": pos.get("strike"),
        "is_call": is_call, "entry": pos.get("entry_price"),
        "price": now_px, "pct": _pct(now_px, pos.get("entry_price")),
        "pressure": contract_pressure(sym, date=pos.get("entry_date")),
        "strike_net": strike_net(pos.get("ticker", ""), pos.get("strike"),
                                 is_call),
        "minutes_to_close": market.minutes_to_close(),
        "held_min": _held_minutes(pos),
        "tech": tech,
    }
    return facts


def _held_minutes(pos):
    try:
        started = datetime.datetime.fromisoformat(pos["entry_at"])
    except (KeyError, ValueError, TypeError):
        return None
    return int((datetime.datetime.now() - started).total_seconds() // 60)


def verdict(f):
    """-> (action, reasons). action is 'اخرج' | 'راقب' | 'امسك'.

    Ordered by how little argument each one takes. The idea being dead beats
    a profit target, because a target reached on a setup that has already
    broken is a number about to be given back.

    Nothing here forecasts. Every reason is a fact from `read`, and a fact
    that could not be read is said to be missing rather than assumed benign.
    """
    out, why = "امسك", []
    tech = f.get("tech")

    # 1. the idea is dead — the stock is back through the level that started it
    if tech and tech.get("level") and tech.get("close"):
        broke_back = (tech["close"] < tech["level"] if f["is_call"]
                      else tech["close"] > tech["level"])
        if broke_back:
            return "اخرج", [f"السهم رجع {'تحت' if f['is_call'] else 'فوق'} "
                            f"{tech['level']:g} — الفكرة انتهت"]

    # 2. the clock. A same-day contract is not a position after the bell.
    left = f.get("minutes_to_close")
    if left is not None and 0 < left <= CLOSING_WARN_MIN:
        return "اخرج", [f"باقي {left} دقيقة على الإغلاق — العقد ينتهي اليوم"]

    # 3. the profit is there
    take = C.EXIT_RULES[0][1]
    if f.get("pct") is not None and f["pct"] >= take:
        why.append(f"وصل {f['pct']:+.0f}% — الهدف {take}%")
        out = "اخرج"

    # 4. buyers stopped lifting. Not a forecast: it is who is trading it now.
    p = f.get("pressure")
    if p is None:
        why.append("التداول على العقد خفيف — ما أقدر أقرأ الضغط")
    elif p["ask_share"] < PRESSURE_FLOOR:
        why.append(f"الشراء خف — {p['ask_share']*100:.0f}% عند الطلب في آخر "
                   f"{p['minutes']} دقيقة")
        out = "اخرج" if out == "اخرج" else "راقب"

    # 5. the strike itself is being sold
    net = f.get("strike_net")
    if net is not None and net < 0:
        why.append(f"يبيعون هذا السترايك اليوم — صافي {net/1e6:.1f}M$")
        out = "اخرج" if out == "اخرج" else "راقب"
    elif net is not None and net > 0 and out == "امسك":
        why.append(f"لسه يشترون هذا السترايك — صافي {net/1e6:+.1f}M$")

    if f.get("pct") is not None and out == "امسك":
        why.append(f"الآن {f['pct']:+.1f}%")
    return out, why


def message(f, action, why, asked=False):
    """One short message. `asked` when he asked, rather than being told."""
    head = {"اخرج": "🔴 اخرج", "راقب": "🟡 راقب", "امسك": "🟢 امسك"}[action]
    kind = "كول" if f["is_call"] else "بوت"
    lines = [f"{head} — {f['ticker']} {f['strike']:g} {kind}"]
    if f.get("price") and f.get("entry"):
        lines.append(f"${f['entry']:.2f} ← ${f['price']:.2f} "
                     f"({f['pct']:+.1f}%)")
    lines += [f"• {w}" for w in why]
    if not asked and action == "اخرج":
        lines.append("القرار قرارك — هذا ما أراه الآن")
    return "\n".join(lines)


def answer(pos, tech=None, asked=True):
    f = read(pos, tech=tech)
    action, why = verdict(f)
    return message(f, action, why, asked=asked), action, f
