"""محلل عقود الأوبشن (Optimizer): يبني على نفس آلية جلب سلاسل العقود في
scanner/options.py (نفس مزوّدَي البيانات Yahoo وCBOE)، لكن بفلترة أشد
وتحليل أعمق شبيه بـ OptionStrat -- تسعير نظري، احتمالية ربح، وهدف ربح عند
أقرب مقاومة، لكل عقد مرشّح.

الفلاتر (أشد من scanner.options.best_options البسيطة، ومنفصلة عنها تماماً
-- لا تؤثر إحداهما على الأخرى):
  دلتا بين OPTIMIZER_DELTA_MIN و OPTIMIZER_DELTA_MAX
  أيام حتى الانتهاء (DTE) بين OPTIMIZER_DTE_MIN و OPTIMIZER_DTE_MAX
  حجم تداول >= OPTIMIZER_VOLUME_MIN
  عقود مفتوحة (Open Interest) >= OPTIMIZER_OI_MIN
  تقلب ضمني (IV) < OPTIMIZER_IV_MAX
  نسبة سبريد العرض/الطلب < OPTIMIZER_SPREAD_MAX

الترتيب: بأعلى احتمالية ربح (Probability of Profit) أولاً -- ليس بالأرخص
ولا بأعلى دلتا فقط.
"""
import datetime as dt
import logging
import time

from . import config, options, pricing, probability

log = logging.getLogger(__name__)


def _passes_optimizer_filters(c: dict) -> bool:
    """يتحقق أن العقد c يحقق كل شروط الـOptimizer دفعة واحدة. أي بيانات
    ناقصة (دلتا/تقلب ضمني/سبريد غير محسوبة) تعني رفض العقد مباشرة بدل
    تخمين قيمة له."""
    try:
        delta = c["delta"]
        iv = c["iv"]
        spread_pct = c["spread_pct"]
        if delta is None or iv is None or spread_pct is None:
            return False
        delta = abs(delta)
        return (config.OPTIMIZER_DELTA_MIN <= delta <= config.OPTIMIZER_DELTA_MAX
                and config.OPTIMIZER_DTE_MIN <= c["days"] <= config.OPTIMIZER_DTE_MAX
                and c["volume"] >= config.OPTIMIZER_VOLUME_MIN
                and c["openInterest"] >= config.OPTIMIZER_OI_MIN
                and iv < config.OPTIMIZER_IV_MAX
                and spread_pct < config.OPTIMIZER_SPREAD_MAX)
    except (KeyError, TypeError):
        return False


def _enrich(spot: float, c: dict, resistance: float | None) -> dict | None:
    """يضيف للعقد: التكلفة، نقطة التعادل، احتمالية الربح، وهدف الربح (أقرب
    مقاومة إن وُجدت، وإلا +10% افتراضياً من السعر الحالي). يرجع None لو
    تعذّر حساب احتمالية الربح (بيانات غير كافية) -- عقد بلا احتمالية ربح لا
    يمكن ترتيبه، فلا فائدة من عرضه."""
    strike, premium, iv, days = c["strike"], c["premium"], c["iv"], c["days"]
    be = pricing.breakeven(strike, premium)
    pop = probability.probability_of_profit(spot, be, days, iv)
    if pop is None:
        return None
    target = resistance if resistance is not None else spot * 1.10
    target_profit = pricing.expected_profit(target, strike, premium, days, iv)

    c["cost"] = round(premium * 100, 2)
    c["breakeven"] = round(be, 2)
    c["probability_of_profit"] = round(pop, 1)
    c["target_price"] = round(target, 2)
    c["target_profit"] = round(target_profit, 2) if target_profit is not None else None
    c["target_is_resistance"] = resistance is not None
    return c


def best_contracts(symbol: str, spot: float, resistance: float | None = None) -> list[dict]:
    """أفضل OPTIMIZER_TOP_N عقود Call مؤهلة لهذا السهم، مرتبة بأعلى
    احتمالية ربح. قائمة فارغة تعني عدم وجود عقد يحقق الشروط الأشد أعلاه (أو
    عدم وجود عقود للسهم أصلاً) -- ما يُترجم في bot.py إلى عدم إرسال تنبيه
    لهذا السهم إطلاقاً.

    يعيد استخدام نفس ذاكرة "لا يوجد أوبشن" ومزوّدَي البيانات في
    scanner/options.py، فيرفع نفس استثناءاتها (OptionsFetchError،
    NoNearTermOptions) للمتصل ليتعامل معها بنفس الطريقة المعتادة.
    """
    if options._no_options.get(symbol, 0) > time.time() - options.NO_OPTIONS_TTL:
        return []

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = options._gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        options._no_options[symbol] = time.time()
        return []

    qualified = [c for c in candidates["call"] if _passes_optimizer_filters(c)]
    if not qualified:
        return []

    enriched = [r for c in qualified if (r := _enrich(spot, c, resistance)) is not None]
    enriched.sort(key=lambda c: -c["probability_of_profit"])
    return enriched[:config.OPTIMIZER_TOP_N]
