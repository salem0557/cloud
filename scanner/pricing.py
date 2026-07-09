"""تسعير عقود الأوبشن (Black-Scholes) عند أي سعر سهم وأي تاريخ مستقبلي.

نعتمد نفس معادلة Black-Scholes البسيطة (بدون مكتبات خارجية مثل py_vollib أو
mibian) المستخدمة أصلاً في scanner/options.py لحساب الدلتا والثيتا -- فقط
نوسّعها هنا لحساب القيمة النظرية الكاملة للعقد (السعر)، لا اليونانيات فقط.
هذا يتجنب إضافة تبعية خارجية جديدة (مكتبات كهذه تحتاج أحياناً أدوات بناء C،
وهذا خطر إضافي على النشر -- بالضبط المشكلة التي عطّلت نشر هذا البوت سابقاً
بسبب سطر تالف في requirements.txt) بينما تبقى الحسابات مطابقة رياضياً لما
تنتجه تلك المكتبات لعقود أوروبية بلا توزيعات أرباح.
"""
import math

RISK_FREE_RATE = 0.045  # ~ عائد أذون الخزانة الحالي


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def theoretical_price(spot: float, strike: float, days: float, iv: float | None,
                      is_call: bool = True) -> float | None:
    """القيمة النظرية للعقد (بريميوم للسهم الواحد) عند سعر سهم `spot` وعدد
    أيام متبقية `days` وتقلب ضمني `iv`.

    عند days<=0 أو iv غير صالح تُستخدم القيمة الجوهرية مباشرة
    (max(spot-strike, 0) لعقد Call) بدل معادلة Black-Scholes التي تنهار
    رياضياً (قسمة على صفر) عند صفر وقت متبقٍ أو صفر تقلب.
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
        return spot * _norm_cdf(d1) - disc_strike * _norm_cdf(d2)
    return disc_strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


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
