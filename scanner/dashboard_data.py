"""يحوّل صفوف signals.db الخام (options/leaps/heavy) إلى قواميس جاهزة
لواجهة لوحة الويب (scanner/webapp.py)، ويولّد رأي "المحلل الذكي" -- تحليل
نصي **آلي مبني على قواعد ثابتة** (POP، الدلتا، IV، اتجاه السوق العام) من
نفس الأرقام التي يحسبها البوت أصلاً، **وليس استدعاءً لنموذج ذكاء اصطناعي
توليدي** (لا مفتاح API، لا تكلفة، لا زمن انتظار شبكي).

ملاحظة مهمة عن البيانات: القيمة المتوقعة (EV) لا تُخزَّن في signals.db
إطلاقاً (فقط تُحسب وتُعرض لحظياً أثناء جلسة /options نفسها) -- لذلك لا
تظهر هنا، خلافاً لرسالة تيليجرام الأصلية للنوع العادي.
"""
import datetime as dt
import re

from . import config, probability_module as pm

_COND_RE = re.compile(r"delta=([\d.]+),\s*iv=(\d+)%,\s*dte=(\d+)")
_DTE_ONLY_RE = re.compile(r"dte=(\d+)")

_DUR_LABELS = {
    "short": "🕐 قصير", "medium": "📅 متوسط",
    "long": "🗓️ طويل (LEAPS)", "far": "🗓️🗓️ LEAPS بعيد",
}
_KIND_LABELS = {"options": "عادي", "leaps": "LEAPS", "heavy": "HEAVY"}
_CATEGORY_LABELS = {"mega": "🏛️ MEGA", "large": "🏢 LARGE", "etf": "📦 ETF"}


def _parse_conditions(conditions: str | None) -> tuple[float | None, float | None, int | None]:
    """(delta, iv, days) من نص conditions المخزَّن -- انظر
    signals_db._row_fields للصيغة الدقيقة اللي يُكتَب بها."""
    if not conditions:
        return None, None, None
    m = _COND_RE.search(conditions)
    if m:
        return float(m.group(1)), float(m.group(2)) / 100.0, int(m.group(3))
    m2 = _DTE_ONLY_RE.search(conditions)
    return None, None, (int(m2.group(1)) if m2 else None)


def _duration_bucket(days: int | None) -> tuple[str, str]:
    if days is None:
        return "unknown", "-"
    if days <= config.OPTIONS_DURATION_SHORT_MAX:
        return "short", _DUR_LABELS["short"]
    if days <= config.OPTIONS_DURATION_MEDIUM_MAX:
        return "medium", _DUR_LABELS["medium"]
    if days <= 730:
        return "long", _DUR_LABELS["long"]
    return "far", _DUR_LABELS["far"]


def _tier_key(pop: float | None) -> str:
    if pop is None:
        return "unknown"
    if pop >= config.OPTIONS_TIER_GOLD:
        return "gold"
    if pop >= config.OPTIONS_TIER_SILVER:
        return "silver"
    return "bronze"


def row_to_contract(row) -> dict:
    """صف SQLite واحد من signals_db.fetch_catalog_signals -> قاموس جاهز
    لـ JSON. `row` يدعم الوصول بالاسم (sqlite3.Row)."""
    delta, iv, days = _parse_conditions(row["conditions"])
    if days is None:
        try:
            days = (dt.date.fromisoformat(row["expiry"]) - dt.date.today()).days
        except (ValueError, TypeError):
            days = None
    dur_key, dur_label = _duration_bucket(days)
    pop = row["probability"]
    tier_key = _tier_key(pop)
    strike = row["strike"]
    premium = row["contract_price"]
    breakeven = round(strike + premium, 2) if (strike is not None and premium is not None) else None
    category = row["filters_matched"] if row["section"] == "heavy" else None

    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "kind": row["section"],
        "kind_label": _KIND_LABELS.get(row["section"], row["section"]),
        "category": category,
        "category_label": _CATEGORY_LABELS.get(category, None),
        "spot": row["underlying_price"],
        "strike": strike,
        "expiry": row["expiry"],
        "days": days,
        "dur_key": dur_key,
        "dur_label": dur_label,
        "premium": premium,
        "cost": round(premium * 100, 2) if premium is not None else None,
        "breakeven": breakeven,
        "delta": delta,
        "iv": iv,
        "pop": pop,
        "tier_key": tier_key,
        "tier_label": pm.tier_label(pop),
        "logged_ts": row["ts"],
        "logged_at": dt.datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M"),
    }


def build_catalog(rows) -> list[dict]:
    return [row_to_contract(r) for r in rows]


# ------------------------------------------------------------ smart analyst

_DISCLAIMER = ("هذا تحليل آلي مبني على قواعد ثابتة (احتمالية الربح، الدلتا، "
              "التقلب الضمني، واتجاه السوق العام) من نفس أرقام البوت -- "
              "وليس ذكاءً اصطناعياً توليدياً ولا توصية استثمارية مباشرة.")


def generate_analyst_opinion(contract: dict, market: dict) -> dict:
    """يبني رأياً نصياً بالعربية من أرقام عقد واحد + حالة السوق العام،
    بمنطق شرطي بحت (بلا نموذج AI) -- انظر docstring الملف. Verdict:
    favorable (مرشح جيد) / neutral (مقبول بحذر) / caution (يحتاج حذراً)."""
    bullets: list[str] = []
    pop = contract.get("pop")
    tier = contract.get("tier_key", "unknown")
    delta = contract.get("delta")
    iv = contract.get("iv")
    spot = contract.get("spot")
    breakeven = contract.get("breakeven")
    dur_key = contract.get("dur_key")

    if pop is not None:
        if tier == "gold":
            bullets.append(f"🎯 احتمالية الربح {pop:.0f}% — ضمن الفئة الذهبية 🥇، من أعلى ما "
                           "يرصده البوت حالياً.")
        elif tier == "silver":
            bullets.append(f"🎯 احتمالية الربح {pop:.0f}% — فئة فضية 🥈، جيدة لكن ليست الأعلى.")
        else:
            bullets.append(f"🎯 احتمالية الربح {pop:.0f}% — عند الحد الأدنى المقبول 🥉 "
                           f"({config.OPTIONS_MIN_POP:.0f}%)، هامش الأمان أضيق من العقود الذهبية.")
    else:
        bullets.append("🎯 احتمالية الربح غير متوفرة لهذا العقد.")

    if delta is not None:
        itm_pct = delta * 100
        depth = "قريبة من النقود العميقة (سلوكها أقرب للسهم نفسه)" if delta >= 0.65 \
            else "معتدلة، توازن بين التكلفة والحساسية لحركة السهم"
        bullets.append(f"الدلتا {delta:.2f} تعني تقريباً {itm_pct:.0f}% احتمالية إغلاق العقد "
                       f"داخل النقود عند الانتهاء (Black-Scholes) — {depth}.")

    if iv is not None:
        if iv < 0.15:
            bullets.append(f"التقلب الضمني منخفض ({iv * 100:.0f}%) — بريميوم زمني رخيص نسبياً، "
                           "لكن هذا يعني أيضاً أن السوق لا يتوقع حركة كبيرة بالسهم.")
        elif iv < 0.25:
            bullets.append(f"التقلب الضمني معتدل ({iv * 100:.0f}%).")
        else:
            bullets.append(f"التقلب الضمني قريب من الحد الأعلى المسموح ({iv * 100:.0f}% من "
                           f"أصل {config.OPTIONS_IV_MAX * 100:.0f}%) — بريميوم أغلى نسبياً لكل "
                           "وحدة زمن.")

    if breakeven is not None and spot:
        needed_pct = (breakeven - spot) / spot * 100
        bullets.append(f"يحتاج السهم لارتفاع {needed_pct:.1f}% من سعره الحالي "
                       f"({spot:.2f}$) ليصل نقطة التعادل ({breakeven:.2f}$).")

    if dur_key in ("short",):
        bullets.append("مدة قصيرة نسبياً — انتبه لتسارع تآكل القيمة الزمنية (theta) كلما "
                       "اقترب الانتهاء.")
    elif dur_key in ("long", "far"):
        bullets.append("مدة طويلة (LEAPS) — تمنح وقتاً أكبر للفكرة لتتحقق، لكنها تربط رأس "
                       "المال لفترة أطول.")

    mood = market.get("mood", "غير متاح") if market else "غير متاح"
    market_favorable = mood in ("متفائل", "متفائل بحذر")
    market_fearful = "خوف" in mood
    if market and mood != "غير متاح":
        if market_favorable:
            bullets.append(f"السوق العام يميل للتفاؤل حالياً ({mood}) — بيئة داعمة نسبياً "
                           "لمراهنات CALL.")
        elif market_fearful:
            bullets.append(f"السوق العام يُظهر قلقاً واضحاً حالياً ({mood}) — توقّع تقلباً "
                           "أعلى من المعتاد حتى على الأسهم الفردية القوية.")
        else:
            bullets.append(f"السوق العام محايد نسبياً حالياً ({mood}).")

    if tier == "gold" and not market_fearful:
        verdict_key, verdict_label = "favorable", "مرشح جيد للمراجعة اليدوية"
    elif tier == "bronze" or market_fearful:
        verdict_key, verdict_label = "caution", "يحتاج حذراً إضافياً"
    else:
        verdict_key, verdict_label = "neutral", "مقبول ضمن الحدود المعتادة"

    return {
        "verdict_key": verdict_key,
        "verdict_label": verdict_label,
        "bullets": bullets,
        "disclaimer": _DISCLAIMER,
    }
