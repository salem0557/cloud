"""The decision layer: turns the indicator snapshot into one stated call —
direction, conviction, entry, stop, targets, risk/reward, invalidation.

Deliberately deterministic. The model that writes the Arabic text afterwards
only explains this verdict; it never sets the numbers, so the same chart
always produces the same call and the same stop.

Sizing follows Salem's standing rule for this repository: the stop is the
lever, not the target. A small stop with a realistic first target beats a
wide stop chasing a multiple.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

BULL = "صاعد"
BEAR = "هابط"
FLAT = "عرضي"


@dataclass
class Signal:
    name: str
    points: float     # signed, -2..+2
    weight: float
    note_ar: str

    @property
    def score(self) -> float:
        return self.points * self.weight


@dataclass
class Verdict:
    direction: str          # صاعد / هابط / عرضي
    side: str               # long / short / none
    score: float            # -100..+100
    conviction: int         # 0..100
    conviction_ar: str
    signals: list[Signal] = field(default_factory=list)
    entry: float | None = None
    entry_note_ar: str = ""
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    rr: float | None = None
    breakeven_rate: float | None = None
    risk_pct: float | None = None
    reward_pct: float | None = None
    invalidation_ar: str = ""
    expected_range: tuple[float, float] | None = None
    conflicts: list[str] = field(default_factory=list)
    plan_valid: bool = True

    def to_dict(self) -> dict:
        return {
            "direction": self.direction, "side": self.side,
            "score": round(self.score, 1), "conviction": self.conviction,
            "conviction_ar": self.conviction_ar,
            "entry": self.entry, "entry_note": self.entry_note_ar,
            "stop": self.stop, "targets": self.targets,
            "risk_pct": self.risk_pct, "reward_pct": self.reward_pct,
            "rr": self.rr, "breakeven_rate": self.breakeven_rate,
            "invalidation": self.invalidation_ar,
            "expected_range": list(self.expected_range) if self.expected_range else None,
            "conflicts": self.conflicts,
            "signals": [{"name": s.name, "points": s.points, "weight": s.weight,
                         "note": s.note_ar} for s in self.signals],
        }


def _digits(price: float) -> int:
    return 4 if price < 1 else 3 if price < 10 else 2


def _r(value: float | None, price: float) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), _digits(price))


def _conviction_label(conviction: int) -> str:
    if conviction >= 75:
        return "قوية جداً"
    if conviction >= 60:
        return "قوية"
    if conviction >= 45:
        return "متوسطة"
    if conviction >= 30:
        return "ضعيفة"
    return "غير كافية"


def _collect(facts: dict, context: dict | None) -> list[Signal]:
    trend = facts["trend"]
    mom = facts["momentum"]
    vol = facts["volume"]
    vola = facts["volatility"]
    lv = facts["levels"]
    signals: list[Signal] = []
    add = signals.append

    # 1. Moving-average structure — the backbone of any timeframe read.
    stack = trend["ema_stack"]
    pts = 2.0 if stack == "bullish" else -2.0 if stack == "bearish" else 0.0
    add(Signal("ema_stack", pts, 1.6,
               {"bullish": "ترتيب المتوسطات صاعد (20>50>200)",
                "bearish": "ترتيب المتوسطات هابط (20<50<200)",
                "mixed": "المتوسطات متشابكة بلا ترتيب واضح"}[stack]))

    slow_gap = trend.get("price_vs_ema_slow_pct")
    if slow_gap is not None:
        pts = 1.5 if slow_gap > 1 else -1.5 if slow_gap < -1 else 0.0
        add(Signal("above_ema200", pts, 1.2,
                   f"السعر {'فوق' if slow_gap > 0 else 'تحت'} متوسط 200 بنسبة {abs(slow_gap):.1f}%"))

    # 2. Price structure (higher highs / lower lows).
    struct = trend["structure"]
    pts = {"uptrend": 2.0, "downtrend": -2.0, "expanding": 0.0,
           "contracting": 0.0, "unclear": 0.0}[struct]
    add(Signal("structure", pts, 1.4, trend["structure_ar"]))

    # 3. Trend strength gate: ADX under 20 means no trend to ride.
    adx = trend.get("adx")
    if adx is not None:
        if adx >= 25:
            direction = 1.0 if (trend.get("di_plus") or 0) > (trend.get("di_minus") or 0) else -1.0
            add(Signal("adx", direction * 1.5, 1.1, f"ADX {adx:.0f} يدل على اتجاه فعّال"))
        elif adx < 18:
            add(Signal("adx", 0.0, 1.1, f"ADX {adx:.0f} ضعيف — سوق عرضي لا اتجاه"))
        else:
            add(Signal("adx", 0.0, 1.1, f"ADX {adx:.0f} في المنطقة الرمادية"))

    # 4. Momentum.
    rsi = mom["rsi"]
    rsi_prev = mom.get("rsi_prev") or rsi
    if rsi >= 70:
        add(Signal("rsi", -0.5 if rsi >= 80 else 0.5, 1.2,
                   f"RSI {rsi:.0f} تشبع شرائي — قوة لكن مخاطرة ارتداد"))
    elif rsi <= 30:
        add(Signal("rsi", 0.5 if rsi <= 20 else -0.5, 1.2,
                   f"RSI {rsi:.0f} تشبع بيعي — ضعف لكن احتمال ارتداد"))
    else:
        pts = (rsi - 50) / 20
        arrow = "يصعد" if rsi > rsi_prev else "ينزل"
        add(Signal("rsi", max(-1.5, min(1.5, pts)), 1.2, f"RSI {rsi:.0f} و{arrow}"))

    hist = mom.get("macd_hist") or 0
    cross = mom.get("macd_cross")
    pts = 2.0 if cross == "bullish" else -2.0 if cross == "bearish" else (
        1.0 if hist > 0 else -1.0 if hist < 0 else 0.0)
    note = ("تقاطع MACD إيجابي جديد" if cross == "bullish" else
            "تقاطع MACD سلبي جديد" if cross == "bearish" else
            f"هيستوجرام MACD {'إيجابي' if hist > 0 else 'سلبي' if hist < 0 else 'محايد'}")
    add(Signal("macd", pts, 1.3, note))

    if mom.get("rsi_divergence") == "bullish":
        add(Signal("divergence", 1.5, 1.3, "دايفرجنس إيجابي: قاع أدنى مع RSI أعلى"))
    elif mom.get("rsi_divergence") == "bearish":
        add(Signal("divergence", -1.5, 1.3, "دايفرجنس سلبي: قمة أعلى مع RSI أدنى"))

    # 5. Volume confirmation — direction of the last bar matters here.
    rel = vol.get("relative")
    change = facts.get("change_pct") or 0
    if rel:
        if rel >= 1.8:
            add(Signal("volume", 1.5 if change > 0 else -1.5, 1.3,
                       f"فوليوم {rel:.1f}× المعدل مع شمعة {'صاعدة' if change > 0 else 'هابطة'}"))
        elif rel <= 0.6:
            add(Signal("volume", 0.0, 1.0, f"فوليوم ضعيف {rel:.1f}× — حركة بلا اقتناع"))
        else:
            add(Signal("volume", 0.5 if change > 0 else -0.5, 0.9,
                       f"فوليوم طبيعي {rel:.1f}×"))
    if vol.get("obv_direction") in ("up", "down"):
        add(Signal("obv", 1.0 if vol["obv_direction"] == "up" else -1.0, 0.9,
                   "OBV يشير إلى " + ("تجميع" if vol["obv_direction"] == "up" else "تصريف")))

    # 6. Location inside the range.
    pb = vola.get("percent_b")
    if pb is not None:
        if pb >= 95:
            add(Signal("location", -0.5, 1.0, "السعر ملتصق بالبولنجر العلوي — ممتد"))
        elif pb <= 5:
            add(Signal("location", 0.5, 1.0, "السعر عند البولنجر السفلي — مضغوط"))
        else:
            add(Signal("location", (pb - 50) / 40, 0.8, f"موقع السعر داخل البولنجر {pb:.0f}%"))
    if vola.get("squeeze"):
        add(Signal("squeeze", 0.0, 1.0, "انضغاط تقلب (BB squeeze) — انفجار حركة قريب بالاتجاهين"))

    high52 = lv.get("pct_from_52w_high")
    if high52 is not None and high52 > -3:
        add(Signal("52w", 1.0, 0.9, "السعر قريب من قمة 52 أسبوع — قوة نسبية"))
    elif high52 is not None and (lv.get("pct_above_52w_low") or 100) < 8:
        add(Signal("52w", -1.0, 0.9, "السعر قريب من قاع 52 أسبوع — ضعف نسبي"))

    # 7. Candles.
    bullish_patterns = {"bullish_engulfing", "hammer", "marubozu_bull", "breakout_20", "gap_up"}
    bearish_patterns = {"bearish_engulfing", "shooting_star", "marubozu_bear",
                        "breakdown_20", "gap_down", "hanging_man"}
    found = set(facts.get("patterns") or [])
    for pattern in found & bullish_patterns:
        add(Signal(f"pattern:{pattern}", 1.0, 0.8, "نموذج شمعة إيجابي: " + pattern))
    for pattern in found & bearish_patterns:
        add(Signal(f"pattern:{pattern}", -1.0, 0.8, "نموذج شمعة سلبي: " + pattern))

    # 8. Higher timeframe alignment — weighted heavily on purpose.
    if context:
        ctx_stack = context["trend"]["ema_stack"]
        ctx_struct = context["trend"]["structure"]
        pts = 0.0
        if ctx_stack == "bullish" or ctx_struct == "uptrend":
            pts += 1.0
        if ctx_stack == "bearish" or ctx_struct == "downtrend":
            pts -= 1.0
        add(Signal("higher_tf", max(-2.0, min(2.0, pts * 2)), 1.5,
                   f"الفريم الأعلى ({context['frame']}): {context['trend']['structure_ar']}"))
    return signals


def _plan(facts: dict, side: str, atr: float) -> dict:
    """Entry / stop / targets from real levels, ATR-bounded."""
    price = facts["price"]
    lv = facts["levels"]
    trend = facts["trend"]
    support = lv.get("nearest_support")
    resistance = lv.get("nearest_resistance")
    supports = [s["price"] for s in lv.get("supports", [])]
    resistances = [r["price"] for r in lv.get("resistances", [])]
    ema_fast = trend.get("ema_fast")
    extended = abs(trend.get("price_vs_ema_fast_pct") or 0) > max(2.0, atr / price * 200)

    if side == "long":
        entry, note = price, "دخول على السعر الحالي"
        if extended and ema_fast and ema_fast < price:
            entry = (price + ema_fast) / 2
            note = "السعر ممتد عن متوسط 20 — الأفضل انتظار ارتداد لمنطقة الدخول"
        struct_stop = (support - 0.25 * atr) if support else None
        atr_stop = entry - 1.2 * atr
        stop = max(struct_stop, atr_stop) if struct_stop else atr_stop
        stop = min(stop, entry - 0.6 * atr)          # never inside the noise
        stop = max(stop, entry - 2.5 * atr)          # never a runaway stop
        targets = [t for t in resistances if t > entry + 0.3 * atr][:3]
        while len(targets) < 2:
            targets.append(entry + (1.5 + len(targets)) * atr)
        label = facts.get("frame_label") or facts["frame"]
        invalidation = f"إغلاق شمعة {label} تحت {round(stop, _digits(price))} يلغي السيناريو"
    elif side == "short":
        entry, note = price, "دخول على السعر الحالي"
        if extended and ema_fast and ema_fast > price:
            entry = (price + ema_fast) / 2
            note = "السعر ممتد تحت متوسط 20 — الأفضل انتظار ارتداد لمنطقة الدخول"
        struct_stop = (resistance + 0.25 * atr) if resistance else None
        atr_stop = entry + 1.2 * atr
        stop = min(struct_stop, atr_stop) if struct_stop else atr_stop
        stop = max(stop, entry + 0.6 * atr)
        stop = min(stop, entry + 2.5 * atr)
        targets = [t for t in sorted(supports, reverse=True) if t < entry - 0.3 * atr][:3]
        while len(targets) < 2:
            targets.append(entry - (1.5 + len(targets)) * atr)
        label = facts.get("frame_label") or facts["frame"]
        invalidation = f"إغلاق شمعة {label} فوق {round(stop, _digits(price))} يلغي السيناريو"
    else:
        return {"entry": None, "stop": None, "targets": [], "note": "", "invalidation":
                "لا خطة تداول واضحة على هذا الفريم — السوق عرضي، الانتظار أفضل",
                "rr": None, "risk_pct": None, "reward_pct": None, "breakeven": None}

    risk = abs(entry - stop)
    reward = abs(targets[0] - entry) if targets else None
    rr = round(reward / risk, 2) if reward and risk else None
    return {
        "entry": _r(entry, price), "stop": _r(stop, price),
        "targets": [_r(t, price) for t in targets],
        "note": note, "invalidation": invalidation, "rr": rr,
        "risk_pct": round(risk / entry * 100, 2),
        "reward_pct": round(reward / entry * 100, 2) if reward else None,
        "breakeven": round(risk / (reward + risk) * 100, 1) if reward else None,
    }


def decide(facts: dict, context: dict | None = None, horizon_bars: int = 10) -> Verdict:
    """The one call the whole answer is built around."""
    signals = _collect(facts, context)
    max_score = sum(abs(s.weight) * 2 for s in signals) or 1.0
    raw = sum(s.score for s in signals)
    score = max(-100.0, min(100.0, raw / max_score * 100))

    if score >= 30:
        direction, side = BULL, "long"
    elif score <= -30:
        direction, side = BEAR, "short"
    elif score >= 12:
        direction, side = "صاعد بميل ضعيف", "long"
    elif score <= -12:
        direction, side = "هابط بميل ضعيف", "short"
    else:
        direction, side = FLAT, "none"

    # 12 + 1.25x: a fully aligned chart reads 80-90%, a mixed one stays under 40%.
    conviction = int(min(92, max(5, 12 + abs(score) * 1.25))) if abs(score) >= 12 \
        else int(max(5, abs(score)))
    adx = facts["trend"].get("adx")
    conflicts: list[str] = []
    if adx is not None and adx < 18 and side != "none":
        conviction = int(conviction * 0.75)
        conflicts.append("ADX ضعيف: الاتجاه غير فعّال، احتمال التذبذب العرضي مرتفع")
    if context:
        ctx_struct = context["trend"]["structure"]
        if side == "long" and ctx_struct == "downtrend":
            conviction = int(conviction * 0.7)
            conflicts.append(f"تعارض: الفريم الأعلى ({context['frame']}) هابط — الصفقة عكس الاتجاه الأكبر")
        if side == "short" and ctx_struct == "uptrend":
            conviction = int(conviction * 0.7)
            conflicts.append(f"تعارض: الفريم الأعلى ({context['frame']}) صاعد — الصفقة عكس الاتجاه الأكبر")
    if facts["volume"].get("dry") and side != "none":
        conflicts.append("الفوليوم ضعيف: الحركة تحتاج تأكيد بسيولة أعلى")
    if facts["volatility"].get("squeeze"):
        conflicts.append("انضغاط تقلب: الحركة القادمة قد تكون عنيفة بالاتجاهين")

    price = facts["price"]
    atr = facts["volatility"]["atr"] or price * 0.01
    plan = _plan(facts, side, atr)
    if plan["rr"] is not None and plan["rr"] < 1:
        # A first target closer than the stop needs a better price, not optimism.
        conflicts.append(f"العائد/المخاطرة {plan['rr']} أقل من 1: المقاومة قريبة — "
                         "الأفضل انتظار سعر أقرب للدعم أو تجاوز المقاومة بإغلاق")

    # Honest expected range: ATR scaled by sqrt(time), biased by the call.
    drift = (score / 100) * atr * math.sqrt(horizon_bars) * 0.5
    spread = atr * math.sqrt(horizon_bars)
    expected = (_r(price + drift - spread / 2, price), _r(price + drift + spread / 2, price))

    return Verdict(
        direction=direction, side=side, score=score, conviction=conviction,
        conviction_ar=_conviction_label(conviction), signals=signals,
        entry=plan["entry"], entry_note_ar=plan["note"], stop=plan["stop"],
        targets=plan["targets"], rr=plan["rr"], breakeven_rate=plan["breakeven"],
        risk_pct=plan["risk_pct"], reward_pct=plan["reward_pct"],
        invalidation_ar=plan["invalidation"], expected_range=expected,
        conflicts=conflicts, plan_valid=side != "none",
    )


def position_size(entry: float, stop: float, account: float, risk_pct: float = 1.0) -> dict:
    """Shares/contracts for a fixed money risk — the only sizing that respects a stop."""
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return {"units": 0, "risk_amount": 0.0}
    risk_amount = account * risk_pct / 100
    return {
        "units": int(risk_amount // risk_per_unit),
        "risk_amount": round(risk_amount, 2),
        "risk_per_unit": round(risk_per_unit, 4),
    }
