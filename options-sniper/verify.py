"""The last gate before Telegram: does this alert contradict itself?

Salem, 2026-09-09:

    "لا اريد ان اشتري عقد ترسل تنبيه عليه ويكون مقلب بسبب خطاء برمجي ثم
     ارسل لك تقول اوووه اكتشفت خطاء بالكود"

He is not asking for a system with no bugs. Nobody can promise that. He is
asking that a bug never reach him as a WRONG alert — that when the code is
broken, he gets nothing rather than something false.

Every defect this project has shipped had the same shape:

  the scheduler crashed and nothing said so           (NameError, no alerts)
  the candle window came back one bar short           (silently, every scan)
  the level was measured across a gap                 (TSLA, 384 vs 370)
  _wick_back rejected every real breakout             (inverted comparison)
  confirms() never required a close beyond the level  (the message said it did)
  the daily report was unreachable code               (return above its caller)

Not one was a bad judgement call. Every one was the code saying one thing and
doing another, with nothing in between to notice. That gap is what this file
closes: it re-derives the alert's own claims from its own numbers, and any
contradiction becomes NO_TRADE instead of a message.

It cannot catch a wrong IDEA — a level that is real and fails is a losing
trade, not a bug. It catches an alert that is internally impossible: a call
whose target sits below its stop, a put alert carrying a call contract, a
$1.85 contract printed as $185, an expiry that has already passed, a strike
the message shows differently from the data it was built from.

The cost of a false positive here is one missed alert. The cost of a false
negative is Salem's money. The checks are therefore deliberately strict, and
every one of them names what it found.
"""
import datetime

import config as C

TOL = 0.02          # 2% — floats, rounding in the renderer, nothing more


def _sides(direction, tech):
    """Geometry. A call goes UP from its level; a put goes DOWN. Everything
    else about the trade follows from that, so it is checked first."""
    out = []
    level, target, stop = tech.get("level"), tech.get("target"), tech.get("stop")
    if None in (level, target, stop):
        return ["مستوى/هدف/وقف ناقص"]
    if direction == "call":
        if not target > level:
            out.append(f"كول وهدفه {target} تحت مستواه {level}")
        if not stop < level:
            out.append(f"كول ووقفه {stop} فوق مستواه {level}")
    else:
        if not target < level:
            out.append(f"بوت وهدفه {target} فوق مستواه {level}")
        if not stop > level:
            out.append(f"بوت ووقفه {stop} تحت مستواه {level}")
    return out


def _room(tech):
    """The anti-chasing gate, re-checked at the door.

    is_late() already guards it inside evaluate(). This is here because a gate
    that lives in one place is a gate one refactor can walk around, and the
    thing it protects — not buying the top — is the rule Salem asked for by
    name.
    """
    room = tech.get("remaining_atr")
    if room is None:
        return ["المساحة المتبقية غير محسوبة"]
    if room < C.MIN_REMAINING_ATR:
        return [f"باقي {room:.2f} ATR والقاعدة تبي {C.MIN_REMAINING_ATR}"]
    return []


def _atr(tech):
    a = tech.get("atr")
    if not a or a <= 0:
        return ["ATR صفر أو مفقود — كل المسافات محسوبة منه"]
    out = []
    # The target and stop are defined as multiples of ATR from the level. If
    # they were built with a different ATR than the one on the payload, the
    # message's "expected move" is a number from another calculation.
    for name, price, mult in (("الهدف", tech.get("target"), C.TARGET_ATR_MULT),
                              ("الوقف", tech.get("stop"), C.STOP_ATR_MULT)):
        if price is None or tech.get("level") is None:
            continue
        if tech.get("reversal") and name == "الوقف":
            continue        # a reversal stops at the failed wick, by design
        got = abs(price - tech["level"]) / a
        if abs(got - mult) > 0.05:
            out.append(f"{name} يبعد {got:.2f} ATR والقاعدة {mult}")
    return out


def _contract(t, direction, spot):
    """One tier. The contract has to be the trade the alert describes."""
    out = []
    kind = "call" if direction == "call" else "put"
    if t.get("type") != kind:
        out.append(f"تنبيه {direction} ومعه عقد {t.get('type')}")
    ask, bid = t.get("ask"), t.get("bid")
    if not ask or ask <= 0:
        out.append("سعر العقد صفر أو مفقود")
    elif bid is not None and bid > ask:
        out.append(f"العرض {bid} أعلى من الطلب {ask}")
    cost = t.get("cost")
    if ask and cost is not None and abs(cost - ask * 100) > max(1.0, ask * TOL * 100):
        # Salem, 2026-09-08: "سعر العقد اللي جبته غير صحيح". The message
        # showing a price the data does not carry is the exact complaint.
        out.append(f"التكلفة {cost} لا تطابق سعر العقد {ask}×100")
    oi = t.get("open_interest")
    if oi is not None and oi < C.MIN_OPEN_INTEREST:
        out.append(f"فتوحات {oi} تحت الحد {C.MIN_OPEN_INTEREST}")
    exp = (t.get("expiry") or "")[:10]
    if exp:
        try:
            if datetime.date.fromisoformat(exp) < datetime.date.today():
                out.append(f"العقد انتهى بتاريخ {exp}")
        except ValueError:
            out.append(f"تاريخ انتهاء غير مقروء: {exp}")
    else:
        out.append("العقد بلا تاريخ انتهاء")
    d = t.get("delta")
    if d is not None and d != 0:
        if kind == "call" and d < 0:
            out.append(f"كول بدلتا سالبة {d}")
        if kind == "put" and d > 0:
            out.append(f"بوت بدلتا موجبة {d}")
    strike = t.get("strike")
    if strike and spot and (strike > spot * 3 or strike < spot / 3):
        out.append(f"سترايك {strike} بعيد جداً عن السعر {spot}")
    return [f"[{t.get('tier', '?')}] {p}" for p in out]


def _message_matches(msg, p):
    """The text must carry the numbers it was built from.

    Every other check reads the payload. This one reads what Salem will
    actually see, because a renderer bug shows a real payload as wrong prices
    — and the payload checks all pass while it does.
    """
    out = []
    for t in p.get("tiers") or []:
        if not t.get("option_symbol"):
            continue
        ask = t.get("ask")
        if ask and f"{ask:.2f}" not in msg:
            out.append(f"سعر {ask:.2f} مو موجود بالرسالة")
        strike = t.get("strike")
        if strike is not None and f"{strike:g}" not in msg:
            out.append(f"سترايك {strike:g} مو موجود بالرسالة")
    return out


def problems(p, msg=None):
    """-> list of contradictions, in Arabic. Empty means the alert is coherent.

    Coherent is not the same as right. It means nothing in this alert
    contradicts anything else in it.
    """
    tech = p.get("technical") or {}
    direction = p.get("direction")
    if direction not in ("call", "put"):
        return [f"اتجاه غير معروف: {direction}"]
    found = _sides(direction, tech) + _room(tech) + _atr(tech)
    tiers = [t for t in (p.get("tiers") or []) if t.get("option_symbol")]
    if not tiers:
        found.append("ما فيه ولا عقد صالح بالتنبيه")
    for t in tiers:
        found += _contract(t, direction, p.get("spot"))
    if msg is not None:
        found += _message_matches(msg, p)
    return found


def blocks(p, msg=None):
    """-> reason string to refuse the alert, or None to let it go.

    The reason is printed by the caller and the alert is dropped. Nothing here
    ever edits the alert into shape: a message that had to be corrected to be
    sent is a message nobody checked.
    """
    found = problems(p, msg)
    if not found:
        return None
    return " | ".join(found[:4]) + (f" (+{len(found) - 4})" if len(found) > 4 else "")
