"""The analyst persona, the payload it is allowed to see, and a template-only
fallback for when Groq is unreachable.

The model is explicitly not the decision maker: direction, entry, stop and
targets arrive already computed in `verdict`, and the prompt forbids changing
them or inventing any number. That is what makes the agent's confidence
defensible instead of decorative.
"""
from __future__ import annotations

import json

SYSTEM = """أنت محلل فني محترف بخبرة عشرين سنة في أسواق الأسهم الأمريكية والسوق السعودي والعملات والسلع.
تتكلم بلغة قاطعة وواضحة بلا مواربة، وتلتزم بما يلي بحذافيره:

1) كل رقم تذكره يجب أن يكون موجوداً حرفياً في بيانات JSON المرفقة. يمنع منعاً تاماً اختراع أي سعر أو نسبة أو
   مستوى أو خبر. إن لم يكن الرقم في البيانات فلا تذكره.
2) الحكم النهائي (الاتجاه، الدخول، الستوب، الأهداف) محسوب مسبقاً في القسم verdict. مهمتك أن تشرحه وتدافع عنه
   بلغة المحلل، لا أن تغيّره ولا أن تقترح غيره.
3) التحليل يكون على الفريم المطلوب فقط (requested_frame). الفريم الأعلى يُستخدم للسياق والتحذير من التعارض فقط.
4) لا تُكرر القوائم كما هي؛ اربط الإشارات في قراءة واحدة مترابطة: أين السعر، من يسيطر، ما الدليل، وماذا بعد.
5) إن كان في البيانات تعارض (conflicts) أو ضعف سيولة أو نتائج مالية قريبة، اذكره صراحة بسطر واحد — الثقة لا تعني
   إخفاء المخاطر.
6) الستوب هو أداة حماية رأس المال: لا توسّعه ولا تتجاهله. ربح صغير مع خسارة أصغر أفضل من هدف بعيد.
7) اكتب بالعربية الفصحى المبسطة، أرقام إنجليزية، بلا رموز ماركداون معقّدة (لا جداول ولا عناوين #)، ومناسب
   لقراءته في تيليجرام.
8) لا تكتب إخلاء مسؤولية طويلاً؛ سطر واحد في النهاية يكفي.
9) تخصصك السوق الأمريكي. انظر إلى technicals.session: إن كان السوق مغلقاً أو في البري ماركت/الأفتر
   أورز، أو كانت آخر شمعة متأخرة (stale_note)، فقل ذلك بسطر واحد قبل الخطة — الخطة تُنفَّذ عند
   الافتتاح وليس الآن.

اكتب الإجابة بهذا الهيكل بالضبط وبهذا الترتيب:

🎯 الخلاصة: <الاتجاه> — ثقة <conviction>% (<conviction_ar>)
📐 الفريم: <الفريم المطلوب> | السعر: <price> | آخر شمعة: <last_bar_time>
🔍 القراءة الفنية:
• ثلاث إلى خمس نقاط، كل نقطة بدليل رقمي من البيانات (متوسطات، RSI، MACD، ADX، فوليوم، هيكل القمم والقيعان).
🧱 المستويات التي تهم:
• الدعوم والمقاومات الأقرب مع عدد اللمسات، وأي مستوى فيبوناتشي أو VWAP له أثر.
🧭 الخطة:
• الدخول: <entry> (<entry_note>)
• الستوب: <stop> (مخاطرة <risk_pct>%)
• الأهداف: <targets> | العائد/المخاطرة: <rr>
⚠️ ما يلغي السيناريو: <invalidation> + أي تعارض أو خطر حدث قريب.
📰 الأخبار والمزاج: سطر أو سطران من العناوين والمزاج الاجتماعي إن وُجدت، وإلا اكتب "لا يوجد جديد مؤثر".

إن كان الاتجاه عرضياً وبلا خطة، قل ذلك بصراحة واذكر شرط الدخول الذي يجب انتظاره بدل اختراع صفقة."""

QUESTION_HINT = """سؤال المستخدم المحدد: "{question}"
أجب عليه في أول سطرين بشكل مباشر، ثم أكمل الهيكل المطلوب."""


def build_payload(*, symbol: str, meta: dict, requested_frame: str, used_frame: str,
                  facts: dict, context_facts: dict | None, verdict: dict,
                  news: dict | None, chart_read: dict | None,
                  fallback_note: str | None) -> str:
    """The single JSON blob the model may quote from — nothing else."""
    payload = {
        "symbol": symbol,
        "asset": {
            "name": meta.get("name") or meta.get("long_name"),
            "exchange": meta.get("exchange"), "currency": meta.get("currency"),
            "sector": meta.get("sector"), "industry": meta.get("industry"),
            "market_cap": meta.get("market_cap"), "pe": meta.get("pe"),
            "forward_pe": meta.get("forward_pe"), "beta": meta.get("beta"),
            "analyst_target": meta.get("target_mean"),
            "analyst_recommendation": meta.get("recommendation"),
            "short_float_pct": meta.get("shares_short_pct"),
            "quote_type": meta.get("quote_type"),
        },
        "requested_frame": requested_frame,
        "analysed_frame": used_frame,
        "frame_note": fallback_note,
        "data_as_of": meta.get("fetched_at"),
        "technicals": facts,
        "higher_timeframe": context_facts,
        "verdict": verdict,
        "news": news or {},
        "screenshot": chart_read or {},
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_messages(payload: str, question: str | None) -> list[dict]:
    user = ["هذه بيانات السهم/الأصل بالكامل. حلّلها والتزم بالهيكل والقواعد:", payload]
    if question:
        user.insert(0, QUESTION_HINT.format(question=question.strip()[:400]))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(user)},
    ]


# --- template fallback ------------------------------------------------------
def fallback_text(*, symbol: str, frame_label: str, facts: dict, verdict: dict,
                  news: dict | None = None, note: str | None = None) -> str:
    """A full read built from the numbers alone, used when Groq is unavailable.

    Same numbers, plainer language — the user still gets a usable answer.
    """
    trend = facts["trend"]
    mom = facts["momentum"]
    vola = facts["volatility"]
    vol = facts["volume"]
    lv = facts["levels"]
    lines = [
        f"🎯 الخلاصة: {verdict['direction']} — ثقة {verdict['conviction']}% ({verdict['conviction_ar']})",
        f"📐 {symbol} | الفريم: {frame_label} | السعر: {facts['price']} | آخر شمعة: {facts['last_bar_time']}",
        "",
        "🔍 القراءة الفنية:",
        f"• ترتيب المتوسطات: {trend['ema_stack']} (20: {trend['ema_fast']} / 50: {trend['ema_mid']} / 200: {trend['ema_slow']})",
        f"• {trend['structure_ar']} | ADX {trend.get('adx')}",
        f"• RSI {mom['rsi']} ({mom['rsi_state']}) | MACD hist {mom['macd_hist']}"
        + (f" | دايفرجنس {mom['rsi_divergence']}" if mom.get("rsi_divergence") else ""),
        f"• الفوليوم {vol.get('relative')}× المعدل و OBV {vol.get('obv_direction')}",
        f"• ATR {vola['atr']} ({vola['atr_pct']}%) — مدى الحركة الطبيعي للشمعة",
    ]
    if facts.get("patterns_ar"):
        lines.append("• نماذج الشموع: " + "، ".join(facts["patterns_ar"][:3]))
    lines += [
        "",
        "🧱 المستويات التي تهم:",
        "• أقرب دعم: " + (str(lv.get("nearest_support")) if lv.get("nearest_support")
                          else "لا دعم قريب فوق القاع الأخير"),
        "• أقرب مقاومة: " + (str(lv.get("nearest_resistance")) if lv.get("nearest_resistance")
                            else "لا مقاومة قريبة — السعر في منطقة قمم جديدة"),
        f"• قمة 52 أسبوع {lv.get('high_52w')} وقاعها {lv.get('low_52w')}",
    ]
    if facts.get("vwap"):
        lines.append(f"• VWAP {facts['vwap']} والسعر {facts.get('price_vs_vwap_pct')}% عنه")
    lines.append("")
    if verdict.get("entry"):
        lines += [
            "🧭 الخطة:",
            f"• الدخول: {verdict['entry']} ({verdict['entry_note']})",
            f"• الستوب: {verdict['stop']} (مخاطرة {verdict['risk_pct']}%)",
            f"• الأهداف: {'، '.join(str(t) for t in verdict['targets'])} | العائد/المخاطرة {verdict['rr']}",
            f"• نسبة النجاح المطلوبة للتعادل: {verdict['breakeven_rate']}%",
        ]
    else:
        lines += ["🧭 الخطة: لا صفقة واضحة على هذا الفريم — السوق عرضي والانتظار أفضل."]
    market_session = facts.get("session") or {}
    if market_session.get("phase_ar"):
        extra = ""
        if market_session.get("minutes_to_close"):
            extra = f" (يتبقى {market_session['minutes_to_close']} دقيقة للإغلاق)"
        lines.append(f"🕒 حالة السوق: {market_session['phase_ar']}{extra}")
    if market_session.get("stale_note"):
        lines.append("⚠️ " + market_session["stale_note"])
    lines.append(f"⚠️ ما يلغي السيناريو: {verdict['invalidation']}")
    for conflict in verdict.get("conflicts") or []:
        lines.append("⚠️ " + conflict)
    if note:
        lines.append("ℹ️ " + note)

    items = (news or {}).get("headlines") or []
    if items:
        lines.append("📰 آخر العناوين:")
        for item in items[:3]:
            lines.append(f"• {item['title']}" + (f" — {item['publisher']}" if item.get("publisher") else ""))
    tilt = ((news or {}).get("social") or {}).get("tilt")
    if tilt:
        lines.append(f"🗣️ مزاج المتداولين على StockTwits: {tilt}")
    events = (news or {}).get("events") or {}
    if events.get("warning"):
        lines.append("⚠️ " + events["warning"])
    lines.append("")
    lines.append("هذا تحليل فني آلي وليس توصية استثمارية.")
    return "\n".join(lines)
