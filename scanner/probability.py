"""احتمالية الربح (Probability of Profit) لعقد Call طويل.

محسوبة عبر التوزيع اللوغاريتمي الطبيعي لسعر السهم عند الانتهاء (نفس افتراض
حركة السعر في Black-Scholes)، وليس تقريب الدلتا الأبسط (وإن كان مذكوراً
كخيار بديل) -- التوزيع اللوغاريتمي أدق لأنه يقيس الاحتمال مقابل نقطة
التعادل الفعلية (سعر التنفيذ + البريميوم المدفوع)، لا سعر التنفيذ وحده كما
تفعل الدلتا تقريبياً.
"""
import math

RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probability_of_profit(spot: float, breakeven_price: float, days: float,
                          iv: float | None) -> float | None:
    """احتمال أن يقفل السهم أعلى من نقطة التعادل عند الانتهاء (نسبة مئوية)،
    بافتراض حركة سعرية لوغاريتمية طبيعية بتقلب `iv` السنوي حول عائد خالٍ من
    المخاطر. None إذا كانت المدخلات غير صالحة لحساب حقيقي (سعر أو أيام أو
    تقلب غير موجب)."""
    if spot <= 0 or breakeven_price <= 0 or days <= 0 or not iv or iv <= 0:
        return None
    t = days / 365.0
    d2 = (math.log(spot / breakeven_price) + (RISK_FREE_RATE - 0.5 * iv * iv) * t) \
        / (iv * math.sqrt(t))
    return _norm_cdf(d2) * 100
