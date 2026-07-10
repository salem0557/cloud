"""رياضيات Black-Scholes الكاملة لوحدة الأوبشن: التسعير النظري، احتمالية
الربح (Probability of Profit)، والقيمة المتوقعة (Expected Value).

يستخدم scipy.stats.norm.cdf بدل تطبيق يدوي عبر math.erf -- بناءً على طلب
صريح، وscipy مكتبة مستقرة جداً وواسعة الانتشار (لا تحتاج أدوات بناء C وقت
التثبيت مثل بعض مكتبات pricing المتخصصة)، بخلاف الحالة السابقة التي دفعتنا
لتجنب تبعيات خارجية جديدة.
"""
import math

from scipy.stats import norm

RISK_FREE_RATE = 0.045  # ~ عائد أذون الخزانة الحالي


def theoretical_price(spot: float, strike: float, days: float, iv: float | None,
                      is_call: bool = True) -> float | None:
    """القيمة النظرية للعقد (بريميوم للسهم الواحد) عند سعر سهم `spot` وعدد
    أيام متبقية `days` وتقلب ضمني `iv`.

    عند days<=0 أو iv غير صالح تُستخدم القيمة الجوهرية مباشرة بدل معادلة
    Black-Scholes التي تنهار رياضياً (قسمة على صفر) عند صفر وقت متبقٍ أو
    صفر تقلب.
    """
    if spot <= 0 or strike <= 0:
        return None
    if days <= 0 or not iv or iv <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)

    t = days / 365.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    disc_strike = strike * math.exp(-RISK_FREE_RATE * t)
    if is_call:
        return spot * norm.cdf(d1) - disc_strike * norm.cdf(d2)
    return disc_strike * norm.cdf(-d2) - spot * norm.cdf(-d1)


def breakeven(strike: float, premium: float, is_call: bool = True) -> float:
    """نقطة التعادل: السعر الذي يجب أن يصله السهم عند الانتهاء حتى لا يخسر
    حامل العقد شيئاً (بلا احتساب عمولات الوساطة)."""
    return strike + premium if is_call else strike - premium


def max_loss(premium: float) -> float:
    """أقصى خسارة ممكنة لمشتري العقد الكامل (×100) = كامل البريميوم المدفوع."""
    return premium * 100


def expected_profit(spot_target: float, strike: float, premium: float,
                    days_remaining: float, iv: float | None,
                    is_call: bool = True) -> float | None:
    """الربح (أو الخسارة) الصافي المتوقع للعقد الكامل (×100) لو وصل السهم
    لسعر `spot_target` بعد أن تبقّى `days_remaining` يوماً على الانتهاء."""
    value = theoretical_price(spot_target, strike, days_remaining, iv, is_call)
    if value is None:
        return None
    return (value - premium) * 100


def probability_of_profit(spot: float, breakeven_price: float, days: float,
                          iv: float | None, is_call: bool = True) -> float | None:
    """احتمالية الربح (POP) بنسبة مئوية -- احتمال أن يقفل السهم أعلى من
    نقطة التعادل عند الانتهاء لعقد Call (N(d2))، أو أدنى منها لعقد Put
    (N(-d2))، بافتراض حركة سعرية لوغاريتمية طبيعية بتقلب `iv` السنوي حول
    عائد خالٍ من المخاطر. None إذا كانت المدخلات غير صالحة."""
    if spot <= 0 or breakeven_price <= 0 or days <= 0 or not iv or iv <= 0:
        return None
    t = days / 365.0
    d2 = (math.log(spot / breakeven_price) + (RISK_FREE_RATE - 0.5 * iv * iv) * t) \
        / (iv * math.sqrt(t))
    return (norm.cdf(d2) if is_call else norm.cdf(-d2)) * 100


def expected_value(pop_pct: float, avg_profit: float, max_loss_amount: float) -> float:
    """القيمة المتوقعة للصفقة (بالدولار):
    EV = (POP × متوسط الربح المحتمل) - ((1-POP) × أقصى خسارة)
    متوسط الربح المحتمل = الربح الصافي المتوقع لو وصل السهم لأقرب مقاومة
    (Call) أو أقرب دعم (Put) عند الانتهاء -- محسوب عبر expected_profit
    أعلاه، لا رقماً افتراضياً ثابتاً."""
    pop = pop_pct / 100.0
    return pop * avg_profit - (1 - pop) * max_loss_amount
