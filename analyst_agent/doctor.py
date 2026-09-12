"""Health check: one command that answers "is it actually working?"

Run it in the host's console:

    python -m analyst_agent.doctor

Every check is independent and never raises, so the report always prints in
full — a broken piece shows as ❌ with the reason beside it instead of a
traceback that hides the other nine results.

The same report is available in Telegram as /diag (owners only), and a short
version is logged at startup so the deploy logs alone tell you the state.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from dataclasses import dataclass

import requests

from . import config

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/getMe"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = False      # the agent cannot work at all without this

    @property
    def mark(self) -> str:
        return "✅" if self.ok else ("❌" if self.fatal else "⚠️")


def check_packages() -> Check:
    needed = ["pandas", "numpy", "yfinance", "mplfinance", "matplotlib", "requests"]
    missing = [name for name in needed if not importlib.util.find_spec(name)]
    optional = {"telegram": "بوت BotFather", "telethon": "يوزر بوت", "PIL": "تصغير الصور"}
    have = [label for module, label in optional.items() if importlib.util.find_spec(module)]
    if missing:
        return Check("المكتبات", False, "ناقصة: " + ", ".join(missing) + " — نفّذ pip install -r requirements.txt", True)
    return Check("المكتبات", True, f"كل الأساسيات موجودة (+{', '.join(have) or 'لا شيء اختياري'})")


def check_groq_key() -> Check:
    if not config.GROQ_API_KEY:
        return Check("مفتاح Groq", False, "GROQ_API_KEY غير مضبوط — قراءة الصور والتحليل النصي معطّلان", True)
    key = config.GROQ_API_KEY
    shape = "يبدأ بـ gsk_" if key.startswith("gsk_") else "شكل غير معتاد (تأكد من النسخ)"
    return Check("مفتاح Groq", True, f"موجود ({len(key)} حرف، {shape})")


def check_groq_models() -> Check:
    from . import groq_client

    if not config.GROQ_API_KEY:
        return Check("موديلات Groq", False, "بلا مفتاح، لا يمكن السؤال", True)
    models = groq_client.available_models(force=True)
    if not models:
        return Check("موديلات Groq", False, "تعذّر الوصول إلى Groq — تأكد من المفتاح والاتصال", True)
    text = groq_client.resolve_model("text")
    vision = groq_client.resolve_model("vision")
    return Check("موديلات Groq", True,
                 f"{len(models)} موديل متاح | النصي: {text} | قراءة الصور: {vision}")


def check_groq_call() -> Check:
    from . import groq_client

    if not config.GROQ_API_KEY:
        return Check("مكالمة Groq", False, "بلا مفتاح", True)
    try:
        reply = groq_client.chat(
            [{"role": "user", "content": "رد بكلمة واحدة فقط: جاهز"}],
            kind="text", temperature=0.0, max_tokens=20)
        return Check("مكالمة Groq", True, f"ردّ: {reply.strip()[:40]}")
    except Exception as exc:
        return Check("مكالمة Groq", False, f"فشلت: {exc}"[:200], True)


def check_telegram() -> Check:
    token = (os.getenv("ANALYST_BOT_TOKEN") or "").strip()
    if not token:
        if config.TELEGRAM_SESSION:
            return Check("تلقرام", True, "لا يوجد توكن بوت، لكن يوجد TELEGRAM_SESSION (وضع اليوزر بوت)")
        return Check("تلقرام", False,
                     "ANALYST_BOT_TOKEN غير مضبوط (ولا TELEGRAM_SESSION) — لا واجهة للوكيل", True)
    try:
        resp = requests.get(TELEGRAM_API.format(token=token), timeout=15)
        data = resp.json()
    except Exception as exc:
        return Check("تلقرام", False, f"تعذّر الوصول إلى تلقرام: {exc}"[:200], True)
    if not data.get("ok"):
        return Check("تلقرام", False,
                     f"التوكن مرفوض: {data.get('description', 'unknown')}", True)
    me = data.get("result") or {}
    username = me.get("username", "?")
    reads_all = me.get("can_read_all_group_messages")
    note = ("يقرأ كل رسائل القروب ✅" if reads_all else
            "⚠️ Privacy Mode مفعّل: لن يرى الصور والكلام في القروب — "
            "في @BotFather نفّذ /setprivacy ثم Disable")
    return Check("تلقرام", bool(reads_all), f"@{username} | {note}")


def check_market_data() -> Check:
    from . import frames, market

    try:
        df = market.fetch("AAPL", frames.get("1d"))
    except Exception as exc:
        return Check("بيانات السوق", False, f"فشل التنزيل: {exc}"[:200], True)
    if df is None or df.empty:
        return Check("بيانات السوق", False, "لم تُرجع Yahoo أي شموع لـ AAPL", True)
    last = df.index[-1]
    return Check("بيانات السوق", True,
                 f"AAPL: {len(df)} شمعة يومية، آخر إغلاق {df['Close'].iloc[-1]:.2f} "
                 f"بتاريخ {str(last)[:10]}")


def check_session() -> Check:
    from . import session

    state = session.state()
    extra = ""
    if state.get("minutes_to_close"):
        extra = f" (يتبقى {state['minutes_to_close']} دقيقة للإغلاق)"
    elif state.get("minutes_to_open"):
        extra = f" (يفتح بعد {state['minutes_to_open']} دقيقة)"
    return Check("جلسة السوق", True, f"{state['phase_ar']}{extra} — {state['now_et']}")


def check_chart() -> Check:
    from . import chart, indicators, verdict
    from .tests.conftest import make_df

    try:
        df = make_df(trend=0.4, seed=5)
        facts = indicators.analyze(df, "1h")
        png = chart.render("TEST", df, "1 hour", facts, verdict.decide(facts).to_dict())
    except Exception as exc:
        return Check("رسم التشارت", False, f"فشل: {exc}"[:200])
    if not png:
        return Check("رسم التشارت", False, "لم تُنتج أي صورة")
    return Check("رسم التشارت", True, f"صورة سليمة ({len(png) // 1024} كيلوبايت)")


def check_pipeline(symbol: str = "NVDA") -> Check:
    """The real thing: a full text request end to end."""
    from . import analyst

    try:
        answer = analyst.analyze(f"حلل {symbol} يومي", with_news=False)
    except Exception as exc:
        return Check("التحليل الكامل", False, f"انهار: {exc}"[:200], True)
    if not answer.ok:
        return Check("التحليل الكامل", False, answer.text.replace("\n", " ")[:150], True)
    return Check("التحليل الكامل", True,
                 f"{answer.headline} | تشارت {len(answer.chart_png or b'') // 1024}KB | "
                 f"موديل {answer.used_model or 'قالب بديل'}")


def check_config() -> Check:
    bits = [
        f"السوق: {'أمريكي فقط' if config.US_ONLY else 'كل الأسواق'}",
        f"الفريم الافتراضي: {config.DEFAULT_FRAME}",
        f"تحليل كل الصور: {'نعم' if config.ANSWER_ALL_PHOTOS else 'لا'}",
        f"قروبات مسموحة: {len(config.ALLOWED_CHATS) or 'الكل'}",
        f"مالكون: {len(config.OWNER_IDS) or 'لا أحد'}",
    ]
    return Check("الإعدادات", True, " | ".join(bits))


QUICK = [check_packages, check_groq_key, check_telegram, check_config, check_session]
FULL = QUICK + [check_groq_models, check_groq_call, check_market_data, check_chart,
                check_pipeline]


def run_all(quick: bool = False) -> list[Check]:
    checks: list[Check] = []
    for func in (QUICK if quick else FULL):
        try:
            checks.append(func())
        except Exception as exc:  # a check itself misbehaving must not stop the rest
            checks.append(Check(func.__name__, False, f"الفحص نفسه فشل: {exc}"[:150]))
    return checks


def report(checks: list[Check]) -> str:
    lines = ["🩺 فحص محلل التشارت", ""]
    for check in checks:
        lines.append(f"{check.mark} {check.name}: {check.detail}")
    broken = [c for c in checks if not c.ok and c.fatal]
    warned = [c for c in checks if not c.ok and not c.fatal]
    lines.append("")
    if broken:
        lines.append("النتيجة: ❌ الوكيل لن يعمل — أصلح: " + "، ".join(c.name for c in broken))
    elif warned:
        lines.append("النتيجة: ⚠️ يعمل مع ملاحظات: " + "، ".join(c.name for c in warned))
    else:
        lines.append("النتيجة: ✅ كل شيء سليم، الوكيل جاهز")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    quick = "--quick" in argv
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    checks = run_all(quick=quick)
    print(report(checks))
    return 1 if any(not c.ok and c.fatal for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
