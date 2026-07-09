"""احتمالية الربح (Probability of Profit) لعقد Call طويل.

محسوبة عبر التوزيع اللوغاريتمي الطبيعي لسعر السهم عند الانتهاء (نفس افتراض
حركة السعر في Black-Scholes)، وليس تقريب الدلتا الأبسط (وإن كان مذكوراً
كخيار بديل) -- التوزيع اللوغاريتمي أدق لأنه يقيس الاحتمال مقابل نقطة
التعادل الفعلية (سعر التنفيذ + البريميوم المدفوع)، لا سعر التنفيذ وحده كما
تفعل الدلتا تقريبياً.
"""
import math

import numpy as np

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


def realized_volatility(closes, bars_per_year: float) -> float | None:
    """تقلب سنوي مقدَّر من عوائد الإغلاق التاريخية -- بديل مجاني عن التقلب
    الضمني (لا يوجد سوق خيارات للأسهم أو الكريبتو هنا كما في وحدة الأوبشن)،
    يُستخدم مباشرة كمدخل لـ probability_of_profit أعلاه. None إذا كانت
    البيانات غير كافية لتقدير موثوق."""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < 10:
        return None
    log_ret = np.diff(np.log(closes))
    log_ret = log_ret[np.isfinite(log_ret)]
    if len(log_ret) < 10:
        return None
    vol = float(np.std(log_ret, ddof=0)) * math.sqrt(bars_per_year)
    return vol if vol > 0 else None
