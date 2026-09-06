"""The causal chain behind an alert: stock first, contract second.

Salem asked for the reasoning to read the way he actually thinks:

    "السهم الفلاني كسر المقاومة وسيصل السعر كذا، فإن هذا معناه العقد صاحب
     السترايك كذا سيرتفع، اشتر الآن."

That is a chain of causes, and it runs one way only. The stock breaks a level;
the level implies a target; the target moves the strike from out of the money
toward it; the greeks turn that stock move into a contract move. Every link is
already computed elsewhere in this system — technical.analyse() measures the
break and the target, scoring.expected_profit_pct() turns a stock move into a
contract move through delta and gamma. Nothing here computes anything new. It
puts the existing numbers in causal order and refuses to state a link whose
inputs are missing.

That refusal is the point. The previous system Salem ran printed a win rate
derived from its own option score — a sentence that sounded like analysis and
contained no information. A chain that says "delta is missing, so I cannot tell
you what this contract does when the stock moves" is worth more than a
confident number with nothing behind it.
"""
import config as C


def chain(payload, tier=None):
    """-> an ordered list of links, each {'step', 'text', 'numbers'}.

    `tier` picks which contract the contract-side links describe; the first
    priced tier is used when none is given. A link whose inputs are missing is
    dropped, and `gaps` in the result names what was missing, so a short chain
    reads as a short chain rather than as a confident one.
    """
    tech = payload.get("technical") or {}
    direction = payload.get("direction") or tech.get("direction") or ""
    up = direction == "call"
    spot = _num(payload.get("spot"))
    links, gaps = [], []

    level, close = _num(tech.get("level")), _num(tech.get("close"))
    atr = _num(tech.get("atr"))
    target, stop = _num(tech.get("target")), _num(tech.get("stop"))
    vol = _num(tech.get("volume_ratio"))

    # 1. What the stock did — the only observed fact in the chain.
    if level and close:
        word = "كسر المقاومة" if up else "كسر الدعم"
        beyond = "وأغلق فوقها" if up else "وأغلق تحتها"
        if not tech.get("closed_beyond"):
            beyond = "ولم تُغلق الشمعة بعد خلفها"
        links.append(_link(
            "break",
            f"{payload.get('ticker','')} {word} {level:g} على فريم "
            f"{C.CANDLE_SIZE} {beyond} عند {close:g}"
            + (f"، بحجم {vol:g}× المتوسط" if vol else ""),
            {"level": level, "close": close, "volume_ratio": vol}))
    else:
        gaps.append("لا يوجد كسر مقاس — لا سلسلة بلا مستوى")
        return {"links": [], "gaps": gaps, "direction": direction}

    # 2. Where that break says price goes, and what says otherwise.
    if target and atr:
        distance = abs(target - close)
        links.append(_link(
            "target",
            f"المدى المتوقع {atr:g} لكل شمعة، فالهدف {target:g} — "
            f"على بُعد {distance:.2f}$ من السعر الآن"
            + (f" ({distance/atr:.1f} مدى)" if atr else ""),
            {"target": target, "atr": atr, "distance": round(distance, 2)}))
    else:
        gaps.append("لا هدف محسوب")
    if stop:
        links.append(_link(
            "invalidation",
            f"الفكرة تسقط إذا رجع السعر تحت {stop:g}"
            if up else f"الفكرة تسقط إذا رجع السعر فوق {stop:g}",
            {"stop": stop}))

    # 3. Which strike that move actually reaches — the link Salem named.
    pick = tier or _first_priced(payload)
    if not pick:
        gaps.append("لا عقد ضمن الميزانية — لا يمكن ربط الحركة بعقد")
        return {"links": links, "gaps": gaps, "direction": direction}

    strike, delta = _num(pick.get("strike")), _num(pick.get("delta"))
    ask, cost = _num(pick.get("ask")), _num(pick.get("cost"))
    if strike and spot and target:
        now_state = _moneyness(strike, spot, up)
        then_state = _moneyness(strike, target, up)
        moved = (f"من {now_state} إلى {then_state}"
                 if now_state != then_state else f"يبقى {then_state}")
        links.append(_link(
            "strike",
            f"عند {target:g} يصبح الإضراب {strike:g} {moved}",
            {"strike": strike, "spot": spot, "target": target}))

    # 4. What that means for the contract's price — through the greeks only.
    move = _num(tech.get("expected_move"))
    profit = pick.get("expected_profit_pct")
    if delta and move:
        per_dollar = abs(delta) * 100
        links.append(_link(
            "greeks",
            f"دلتا العقد {abs(delta):.2f} — أي {per_dollar:.0f} سنت لكل دولار "
            f"يتحركه السهم. حركة {move:g}$ ≈ {abs(delta)*move:.2f}$ في العقد",
            {"delta": delta, "expected_move": move}))
    elif move:
        gaps.append("الدلتا غير متاحة — لا يمكن تحويل حركة السهم إلى حركة العقد")

    if profit is not None and cost:
        links.append(_link(
            "contract",
            f"الشراء الآن بـ {ask:g}$ ({cost:.0f}$ للعقد)، "
            f"والتقدير عند بلوغ الهدف {profit:+.0f}% — تقدير بالدلتا وليس وعداً",
            {"ask": ask, "cost": cost, "expected_profit_pct": profit}))
    return {"links": links, "gaps": gaps, "direction": direction}


def as_text(built, bullet="←"):
    """The chain as lines, in the order the causes actually run."""
    out = [f"{bullet} {l['text']}" for l in built.get("links", [])]
    out += [f"⚠️ {g}" for g in built.get("gaps", [])]
    return "\n".join(out)


def _moneyness(strike, price, up):
    """Where a strike sits against a price, in Salem's words."""
    gap = (strike - price) if up else (price - strike)
    if abs(gap) < 0.01:
        return "عند المال"
    return "خارج المال" if gap > 0 else "داخل المال"


def _first_priced(payload):
    for t in payload.get("tiers") or []:
        if t.get("option_symbol") and _num(t.get("ask")):
            return t
    return None


def _link(step, text, numbers):
    return {"step": step, "text": text, "numbers": numbers}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
