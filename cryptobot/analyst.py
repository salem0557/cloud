"""The analyst agent.

It never places an order. It looks at two timeframes, scores the setup out of
100 weighted points, and refuses outright on conditions that historically turn
a scalp into a bag: dead or knife-like volatility, a wide spread, a price
already extended far from its mean, or a stop so far away that the reward does
not justify it.

Only a Verdict with ok=True reaches the trader, and even then the trader still
has to get it past the risk gate.
"""
from dataclasses import dataclass, field

from . import config
from .indicators import (Candle, atr, bollinger, closes, ema, falling_highs,
                         fmt_price, last, macd, recent_swing_high,
                         recent_swing_low, rising_lows, rsi, sma, volumes,
                         vwap)


@dataclass
class Check:
    name: str          # Arabic label shown to the user
    weight: float
    passed: bool
    detail: str = ""


@dataclass
class Verdict:
    symbol: str
    side: str | None = None          # "long" | "short" | None
    ok: bool = False
    score: float = 0.0
    price: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    rr: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    checks: list[Check] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)   # hard refusals
    notes: list[str] = field(default_factory=list)      # context, not refusals

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def passed_checks(self) -> list[Check]:
        return [c for c in self.checks if c.passed]

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def _trend_direction(htf: list[Candle]) -> tuple[str, str]:
    """Higher-timeframe bias: ('up'|'down'|'flat', arabic detail)."""
    hc = closes(htf)
    fast = last(ema(hc, config.EMA_TREND))
    slow = last(ema(hc, config.EMA_TREND_SLOW))
    if fast is None:
        return "flat", "بيانات الفريم الكبير غير كافية"
    price = hc[-1]
    # EMA200 needs a long history; on a young pair fall back to EMA50 vs price.
    if slow is None:
        if price > fast:
            return "up", f"السعر فوق EMA{config.EMA_TREND}"
        return "down", f"السعر تحت EMA{config.EMA_TREND}"
    if fast > slow and price > slow:
        return "up", f"EMA{config.EMA_TREND} فوق EMA{config.EMA_TREND_SLOW}"
    if fast < slow and price < slow:
        return "down", f"EMA{config.EMA_TREND} تحت EMA{config.EMA_TREND_SLOW}"
    return "flat", "الفريم الكبير متذبذب"


def _spread_pct(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    bid, ask = ticker.get("bid"), ticker.get("ask")
    if not bid or not ask or ask <= 0:
        return None
    return (ask - bid) / ask * 100


def analyze(symbol: str, ltf: list[Candle], htf: list[Candle],
            ticker: dict | None = None) -> Verdict:
    """Score one symbol. Pure function — safe to run in any thread."""
    v = Verdict(symbol=symbol)

    need = max(config.EMA_SLOW, config.BB_PERIOD, config.ATR_PERIOD) + 5
    if len(ltf) < need or len(htf) < config.EMA_TREND + 5:
        v.blockers.append("بيانات غير كافية للتحليل")
        return v

    price = ltf[-1].close
    v.price = price
    lc = closes(ltf)
    lv = volumes(ltf)

    atr_val = last(atr(ltf, config.ATR_PERIOD))
    if atr_val is None:
        v.blockers.append("تعذّر حساب التذبذب (ATR)")
        return v
    v.atr = atr_val
    v.atr_pct = atr_val / price * 100
    # A computed zero is not a failure — it is a market with no range at all,
    # and every ATR-scaled number below would divide by it.
    if atr_val <= 0:
        v.blockers.append("السوق ميت — لا يوجد تذبذب يُذكر")
        return v

    # ---------------------------------------------------------- hard refusals
    # These are checked before scoring: no score is high enough to override a
    # market you cannot get in and out of cleanly.
    if v.atr_pct < config.MIN_ATR_PCT:
        v.blockers.append(f"السوق ميت — تذبذب {v.atr_pct:.2f}% أقل من "
                          f"{config.MIN_ATR_PCT}%")
    if v.atr_pct > config.MAX_ATR_PCT:
        v.blockers.append(f"تذبذب عنيف {v.atr_pct:.2f}% أعلى من "
                          f"{config.MAX_ATR_PCT}% — خطر انزلاق")

    spread = _spread_pct(ticker)
    if spread is not None:
        if spread > config.MAX_SPREAD_PCT:
            v.blockers.append(f"الفارق السعري واسع {spread:.3f}%")
        else:
            v.notes.append(f"الفارق السعري {spread:.3f}%")

    if ticker:
        qv = ticker.get("quoteVolume")
        if qv is not None and qv < config.MIN_24H_QUOTE_VOLUME:
            v.blockers.append(f"سيولة 24س ضعيفة ({qv:,.0f}$)")

    # ------------------------------------------------------------- direction
    trend, trend_detail = _trend_direction(htf)
    ema_fast = last(ema(lc, config.EMA_FAST))
    ema_slow = last(ema(lc, config.EMA_SLOW))
    if ema_fast is None or ema_slow is None:
        v.blockers.append("بيانات غير كافية للمتوسطات")
        return v

    if trend == "up":
        side = "long"
    elif trend == "down" and config.shorts_enabled():
        side = "short"
    elif trend == "down":
        v.blockers.append("الاتجاه هابط والبيع المكشوف غير مفعّل")
        side = "long"   # keep scoring so /analyze still explains itself
    else:
        v.blockers.append("لا يوجد اتجاه واضح على الفريم الكبير")
        side = "long" if ema_fast >= ema_slow else "short"
    v.side = side
    long_side = side == "long"

    # Chasing a candle that already ran is the single most expensive scalping
    # habit; measure the distance from the mean in ATRs, not percent.
    extension = abs(price - ema_slow) / atr_val
    if extension > config.MAX_EXTENSION_ATR and (
            (long_side and price > ema_slow) or (not long_side and price < ema_slow)):
        v.blockers.append(f"السعر ممتد {extension:.1f} ATR عن المتوسط — "
                          f"لا نطارد الشمعة")

    # ---------------------------------------------------------------- scoring
    checks: list[Check] = []

    # 1. Higher-timeframe agreement (25) — the scalp must swim downstream.
    checks.append(Check("اتجاه الفريم الكبير", 25.0,
                        (trend == "up" and long_side) or (trend == "down" and not long_side),
                        trend_detail))

    # 2. Low-timeframe momentum alignment (15)
    vwap_val = last(vwap(ltf, 20))
    if long_side:
        mom = ema_fast > ema_slow and (vwap_val is None or price >= vwap_val)
    else:
        mom = ema_fast < ema_slow and (vwap_val is None or price <= vwap_val)
    checks.append(Check("زخم الفريم الصغير", 15.0, mom,
                        f"EMA{config.EMA_FAST}={fmt_price(ema_fast)} / "
                        f"EMA{config.EMA_SLOW}={fmt_price(ema_slow)}"))

    # 3. Pullback then reclaim (15) — we buy the dip inside the trend, not the
    #    breakout. Price must have visited the band/EMA in the last 5 bars and
    #    closed back on the right side.
    lower, mid, upper = bollinger(lc, config.BB_PERIOD, config.BB_STD)
    band_lo, band_hi = lower[-1], upper[-1]
    recent = ltf[-5:]
    if long_side:
        touched = any(c.low <= (band_lo if band_lo else ema_slow) or c.low <= ema_slow
                      for c in recent)
        reclaimed = price > ema_slow
    else:
        touched = any(c.high >= (band_hi if band_hi else ema_slow) or c.high >= ema_slow
                      for c in recent)
        reclaimed = price < ema_slow
    checks.append(Check("ارتداد من منطقة القيمة", 15.0, touched and reclaimed,
                        "لمس المنطقة ثم استعاد" if touched and reclaimed
                        else "لم يرتد من منطقة واضحة"))

    # 4. RSI in the useful band (10) — recovering, not exhausted. Buying an
    #    RSI of 80 on a 5m chart is how a scalp becomes an investment.
    rsi_val = last(rsi(lc, config.RSI_PERIOD))
    if rsi_val is None:
        rsi_ok, rsi_detail = False, "RSI غير متاح"
    elif long_side:
        rsi_ok = 40 <= rsi_val <= 72
        rsi_detail = f"RSI={rsi_val:.1f}"
    else:
        rsi_ok = 28 <= rsi_val <= 60
        rsi_detail = f"RSI={rsi_val:.1f}"
    checks.append(Check("RSI في نطاق صحي", 10.0, rsi_ok, rsi_detail))

    # 5. MACD histogram accelerating in our direction (10)
    _, _, hist = macd(lc)
    h_now, h_prev = hist[-1], hist[-2] if len(hist) > 1 else None
    if h_now is None or h_prev is None:
        macd_ok, macd_detail = False, "MACD غير متاح"
    elif long_side:
        macd_ok = h_now > 0 and h_now > h_prev
        macd_detail = f"الهيستوجرام {h_now:+.6f}"
    else:
        macd_ok = h_now < 0 and h_now < h_prev
        macd_detail = f"الهيستوجرام {h_now:+.6f}"
    checks.append(Check("تسارع MACD", 10.0, macd_ok, macd_detail))

    # 6. Volume confirmation (10) — a move without volume is a move that fades.
    vol_avg = last(sma(lv, 20))
    vol_ok = bool(vol_avg and lv[-1] >= vol_avg * config.VOL_SURGE_MULT)
    checks.append(Check("حجم مؤكِّد", 10.0, vol_ok,
                        f"×{lv[-1] / vol_avg:.2f} من المتوسط" if vol_avg else "غير متاح"))

    # 7. Volatility in the tradeable band (5)
    checks.append(Check("تذبذب مناسب", 5.0,
                        config.MIN_ATR_PCT <= v.atr_pct <= config.MAX_ATR_PCT,
                        f"ATR={v.atr_pct:.2f}%"))

    # 8. Market structure (10)
    struct = rising_lows(ltf) if long_side else falling_highs(ltf)
    wanted = "قيعان صاعدة" if long_side else "قمم هابطة"
    checks.append(Check("بنية السوق", 10.0, struct,
                        wanted if struct else f"لا توجد {wanted}"))

    v.checks = checks
    v.score = round(sum(c.weight for c in checks if c.passed), 1)

    # ------------------------------------------------------------- trade plan
    # Stop goes behind structure or an ATR envelope, whichever is further, so a
    # normal wick does not take us out. Targets are pure R multiples of that.
    if long_side:
        structural = recent_swing_low(ltf, 20)
        stop = min(structural, price - config.SL_ATR_MULT * atr_val)
        stop = min(stop, price * 0.999)          # never a zero-risk stop
        risk = price - stop
        v.tp1 = price + config.TP1_R * risk
        v.tp2 = price + config.TP2_R * risk
    else:
        structural = recent_swing_high(ltf, 20)
        stop = max(structural, price + config.SL_ATR_MULT * atr_val)
        stop = max(stop, price * 1.001)
        risk = stop - price
        v.tp1 = price - config.TP1_R * risk
        v.tp2 = price - config.TP2_R * risk
    v.entry = price
    v.stop = stop

    # Fees eat a scalp alive: charge both sides plus slippage against the
    # reward before judging the ratio, so 1.5R here means 1.5R after costs.
    cost = price * (2 * config.TAKER_FEE_PCT + config.SLIPPAGE_PCT) / 100
    net_reward = abs(v.tp2 - price) - cost
    v.rr = round(net_reward / risk, 2) if risk > 0 else 0.0
    if v.rr < config.MIN_RR:
        v.blockers.append(f"العائد/المخاطرة {v.rr} أقل من {config.MIN_RR}")

    if v.score < config.MIN_SCORE:
        v.blockers.append(f"القوة {v.score:.0f}/100 أقل من الحد {config.MIN_SCORE:.0f}")

    v.ok = not v.blockers
    return v


def format_verdict(v: Verdict) -> str:
    """Telegram-ready Arabic summary of one analysis."""
    head = "🟢 صفقة مقبولة" if v.ok else "⛔️ مرفوضة"
    side = "شراء" if v.side == "long" else "بيع" if v.side == "short" else "—"
    lines = [
        f"<b>{head} — {v.symbol}</b>",
        f"الاتجاه: {side} | القوة: <b>{v.score:.0f}/100</b> | ع/م: {v.rr}",
        f"السعر: {fmt_price(v.price)} | ATR: {v.atr_pct:.2f}%",
    ]
    if v.entry:
        lines += [
            f"الدخول: {fmt_price(v.entry)}",
            f"وقف الخسارة: {fmt_price(v.stop)}",
            f"هدف ١: {fmt_price(v.tp1)} | هدف ٢: {fmt_price(v.tp2)}",
        ]
    lines.append("")
    lines.append("<b>تحليل المحلل:</b>")
    for c in v.checks:
        mark = "✅" if c.passed else "❌"
        lines.append(f"{mark} {c.name} ({c.weight:.0f}) — {c.detail}")
    if v.blockers:
        lines.append("")
        lines.append("<b>أسباب الرفض:</b>")
        lines += [f"• {b}" for b in v.blockers]
    if v.notes:
        lines += [""] + [f"ℹ️ {n}" for n in v.notes]
    return "\n".join(lines)
