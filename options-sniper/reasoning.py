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
        beyond = "وثبت فوقها" if up else "وثبت تحتها"
        if not tech.get("closed_beyond"):
            beyond = "ولسه ما ثبت خلفها"
        links.append(_link(
            "break",
            f"{payload.get('ticker','')} {word} {level:g} {beyond}"
            + (f"، وحجم التداول {vol:g} ضعف المعتاد" if vol else ""),
            {"level": level, "close": close, "volume_ratio": vol}))
    else:
        gaps.append("ما فيه كسر — لا توجد فكرة أصلاً")
        return {"links": [], "gaps": gaps, "direction": direction}

    # 2. Where that break says price goes, and what says otherwise.
    if target:
        distance = abs(target - close)
        way = "يطلع" if up else "ينزل"
        links.append(_link(
            "target",
            f"المتوقع {way} إلى {target:g} — يعني {distance:.2f}$ من سعره الحين",
            {"target": target, "atr": atr, "distance": round(distance, 2)}))
    else:
        gaps.append("ما فيه هدف محسوب")
    if stop:
        back = "رجع تحت" if up else "رجع فوق"
        links.append(_link(
            "invalidation",
            f"لو {back} {stop:g} — الفكرة انتهت، اخرج",
            {"stop": stop}))

    # 3. Which strike that move actually reaches — the link Salem named.
    pick = tier or _first_priced(payload)
    if not pick:
        gaps.append("ما فيه عقد داخل الميزانية")
        return {"links": links, "gaps": gaps, "direction": direction}

    strike, delta = _num(pick.get("strike")), _num(pick.get("delta"))
    ask, cost = _num(pick.get("ask")), _num(pick.get("cost"))
    if strike and spot and target:
        reaches = (target >= strike) if up else (target <= strike)
        links.append(_link(
            "strike",
            f"عند {target:g} يصير عقد {strike:g} رابح"
            if reaches else
            f"عقد {strike:g} يقرب من الربح بس ما يوصله عند {target:g}",
            {"strike": strike, "spot": spot, "target": target}))

    # 4. What that means for the contract's price — through the greeks only.
    move = _num(tech.get("expected_move"))
    profit = pick.get("expected_profit_pct")
    if delta and move:
        cents = abs(delta) * 100
        verb = "يصعده" if up else "ينزله"
        links.append(_link(
            "greeks",
            f"كل دولار {verb} السهم يزيد العقد {cents:.0f} سنت — "
            f"فحركة {move:g}$ تقريباً {abs(delta)*move:+.2f}$",
            {"delta": delta, "expected_move": move}))
    elif move:
        gaps.append("الدلتا ناقصة — ما أقدر أحسب حركة العقد")

    if profit is not None and cost:
        links.append(_link(
            "contract",
            f"تكلفته الحين {cost:.0f}$، ولو وصل الهدف ≈ {profit:+.0f}% (تقدير)",
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
