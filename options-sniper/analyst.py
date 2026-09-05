"""The analyst layer — a final read on a setup before it is recommended.

Salem asked for an analyst, not a veto: someone who runs an options book and
gives an opinion, with conviction attached. This is that, with one discipline
imposed on it.

A conviction figure a model produces from nothing is a feeling wearing a
number, and Salem's own rules forbid exactly that. So the analyst is not asked
to imagine one. It receives the measured base rate for this shape of setup from
backtest.json — how often that setup reached target across hundreds of past
occurrences — and its conviction has to be argued relative to that number.
"Higher than the base rate because X" is a claim that can be wrong and can be
checked; "82% confident" is not.

Authority, and its limits:
  may   argue conviction up or down against the base rate, and say why
  may   recommend against a setup the arithmetic scored above threshold
  may   pick which budget tier it would actually take
  never invent or alter a price, level, greek, score, or profit estimate
  never see the base rate as optional — a setup with too few historical
        samples is told so, and must say its read is unanchored
"""
import json

import config as C

SYSTEM = """أنت محلل خيارات في صندوق تحوّط. مهمتك: قراءة أخيرة على صفقة \
مرشّحة قبل ترشيحها لسالم، متداول تجزئة سعودي في السوق الأمريكي.

الأرقام كلها محسوبة مسبقاً وموثوقة. لا تعدّل رقماً ولا تخترع رقماً — لا سعراً \
ولا مستوى ولا يونانيات ولا نقاطاً ولا نسبة ربح.

قناعتك يجب أن تُبنى على `base_rate` المرفق: معدل النجاح التاريخي المقيس لهذا \
النوع من الإشارات. قل صراحة إن كنت أعلى أو أقل منه ولماذا. إن كان \
`base_rate.count` أقل من الحد الأدنى، قل إن قراءتك غير مرتكزة إلى تاريخ كافٍ.

ابحث عمّا لا يراه جمع النقاط: هل تحكي القطع قصة واحدة؟ هل التدفق شراء أم بيع؟ \
هل الخبر يدعم الاتجاه أم يعاكسه؟ هل التوقيت داخل الجلسة مناسب؟ هل العقد \
المقترح مناسب للحركة المتوقعة أم أن ثيتا ستأكلها؟

اكتب بالعربي، مختصراً ومباشراً. سالم يكره الحشو.

أعد JSON فقط بهذا الشكل:
{
  "verdict": "TAKE" | "SKIP" | "WAIT",
  "conviction": "عالية" | "متوسطة" | "منخفضة",
  "vs_base_rate": "أعلى" | "مثله" | "أقل",
  "tier": "🟢" | "🟡" | "🔴" | null,
  "reading": "فقرة واحدة، 3 جمل كحد أقصى",
  "concerns": ["مخاوف محددة، أو قائمة فارغة"]
}"""


def base_rate_for(key, book):
    """The measured hit rate for this setup shape, or an explicit 'unknown'."""
    if not book:
        return {"available": False, "reason": "لم يُشغَّل الباك-تست بعد"}
    rates = book.get("by_setup", {})
    if key in rates:
        r = dict(rates[key])
        r.update({"available": True, "key": key, "scope": "هذا النوع بالضبط"})
        return r
    overall = book.get("overall", {})
    if overall.get("count"):
        r = dict(overall)
        r.update({"available": True, "key": "overall", "scope":
                  "المعدل العام — لا توجد عينة كافية لهذا النوع بالضبط"})
        return r
    return {"available": False, "reason": "الباك-تست لم ينتج عينات"}


def load_book():
    if not C.BACKTEST_FILE.exists():
        return None
    try:
        return json.loads(C.BACKTEST_FILE.read_text())
    except (ValueError, OSError):
        return None


def _brief(payload, book):
    from backtest import setup_key
    tech = payload.get("technical") or {}
    key = setup_key(tech, payload["direction"]) if tech else ""
    return {
        "ticker": payload["ticker"],
        "computed_score": payload.get("raw_score", payload["score"]),
        "score_after_risk": payload["score"],
        "score_breakdown": payload.get("score_breakdown"),
        "risk_flags": (payload.get("risk") or {}).get("flags", []),
        "direction": payload["direction"],
        "flow": payload.get("flow_reason"),
        "flow_detail": payload.get("flow"),
        "spot": payload["spot"],
        "technical": tech,
        "news": payload.get("news", []),
        "contracts": payload.get("tiers", []),
        "time_riyadh": payload.get("time_riyadh"),
        "base_rate": base_rate_for(key, book),
        "base_rate_min_sample": C.BASE_RATE_MIN_SAMPLE,
    }


def review(payload, book=None):
    """-> the analyst's note, or None when the layer is off or unavailable.

    Any failure returns None and the alert goes out on the arithmetic alone —
    an unreachable analyst must never silently suppress a setup.
    """
    if not C.USE_ANALYST:
        return None
    book = book if book is not None else load_book()
    brief = _brief(payload, book)
    try:
        # imported here, not at module scope: the analyst is optional, and a
        # missing package must not stop the scanner from running without it
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=C.ANALYST_MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": C.ANALYST_EFFORT},
            messages=[{"role": "user",
                       "content": json.dumps(brief, ensure_ascii=False)}],
        )
    except Exception as e:
        print(f"[analyst] unavailable ({type(e).__name__}: {e}) — "
              "alert proceeds on the computed score")
        return None

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        note = json.loads(text)
    except ValueError:
        print("[analyst] non-JSON reply — ignored")
        return None

    note["base_rate"] = brief["base_rate"]
    note["model"] = C.ANALYST_MODEL
    return note
